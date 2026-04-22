#!/usr/bin/env python3
"""LLM clients for SwarmEvolve.

Defines a transport-agnostic ``LLMClient`` protocol plus two concrete
clients:

* ``AnthropicClient`` — wraps the official ``anthropic`` SDK
  (``Messages.create``). Reads the API key and model from environment
  variables (``ANTHROPIC_API_KEY`` / ``ANTHROPIC_MODEL``) so secrets never
  appear on command lines or in logs. Retries transient errors with
  exponential backoff.
* ``MockClient`` — deterministic test double; returns queued responses
  in order. Used by CI so network access is never required.

The ``StubClient`` is retained for callers that want a loud "not
configured" error rather than a live request.

The full Gemini adapter and the evolutionary loop (``scripts/evolve.py``)
remain M10 deliverables; ``AnthropicClient`` lands early so a single-shot
generation (``scripts/evolve_once.py``) can be exercised against the
live API today.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


# ------------------------------------------------------------------------
# Response / protocol shapes
# ------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMResponse:
    """A normalized response shape produced by every client."""
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


class LLMClient(Protocol):
    """Transport-agnostic protocol for prompt → response clients."""

    model: str

    def generate(self, prompt: str, *, max_tokens: int = 4096) -> LLMResponse: ...


# ------------------------------------------------------------------------
# Stub + mock
# ------------------------------------------------------------------------


class StubClient:
    """Placeholder client that refuses to serve."""

    model: str = "stub"

    def generate(self, prompt: str, *, max_tokens: int = 4096) -> LLMResponse:
        raise NotImplementedError(
            "Use AnthropicClient (or MockClient in tests). StubClient is a "
            "no-op kept for regression coverage."
        )


class MockClient:
    """Deterministic test double; returns queued responses in order."""

    def __init__(self, responses: list[LLMResponse] | None = None, model: str = "mock"):
        self._queue: list[LLMResponse] = list(responses or [])
        self.model = model
        self.calls: list[str] = []

    def push(self, response: LLMResponse) -> None:
        self._queue.append(response)

    def generate(self, prompt: str, *, max_tokens: int = 4096) -> LLMResponse:
        self.calls.append(prompt)
        if not self._queue:
            raise RuntimeError("MockClient queue empty")
        return self._queue.pop(0)


# ------------------------------------------------------------------------
# Anthropic
# ------------------------------------------------------------------------


class LLMError(RuntimeError):
    """Raised when an LLM call cannot be completed after retries."""


# Regex used to redact anything that looks like an API key from log output.
# Matches ``sk-ant-...`` (Anthropic) or any 20+ char base64-ish run that
# follows a ``key`` or ``token`` label. Keep conservative — we'd rather
# over-redact than leak.
_SECRET_RE = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{10,}"
    r"|(?:api[_-]?key|token)[=:\s]+[A-Za-z0-9_\-]{20,})",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """Replace anything resembling an API key or bearer token."""
    return _SECRET_RE.sub("***REDACTED***", text)


class AnthropicClient:
    """Thin wrapper over ``anthropic.Anthropic.messages.create``.

    The SDK is imported lazily so the module remains importable on hosts
    that don't install the optional ``llm`` extra.

    Parameters
    ----------
    model
        Model identifier (e.g. ``"claude-3-5-sonnet-20241022"``). If
        ``None`` the value of ``ANTHROPIC_MODEL`` is used, falling back to
        ``"claude-3-5-sonnet-latest"``.
    api_key
        If ``None`` the SDK reads ``ANTHROPIC_API_KEY`` itself. We never
        log or echo the key; callers should prefer the env-var form.
    system
        Optional system prompt prepended to every request.
    max_retries
        Upper bound on retry count for transient errors (rate limit, 5xx).
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        system: str | None = None,
        max_retries: int = 3,
        request_timeout: float = 120.0,
    ):
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised in CI
            raise LLMError(
                "anthropic SDK not installed. Install with `pip install anthropic`."
            ) from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set. Export the key or pass api_key=."
            )

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=key, timeout=request_timeout)
        self.model = model or os.environ.get("ANTHROPIC_MODEL") \
            or "claude-3-5-sonnet-latest"
        self._system = system
        self._max_retries = max(1, int(max_retries))
        self._log = logging.getLogger("swarmevolve.llm.anthropic")

    def generate(self, prompt: str, *, max_tokens: int = 4096) -> LLMResponse:
        """Single-turn user message → assistant text."""
        attempt = 0
        last_exc: Exception | None = None
        while attempt < self._max_retries:
            attempt += 1
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if self._system is not None:
                    kwargs["system"] = self._system
                raw = self._client.messages.create(**kwargs)
                break
            except self._transient_errors() as exc:
                last_exc = exc
                sleep = min(2.0 ** attempt, 30.0)
                self._log.warning(
                    "anthropic-retry attempt=%d sleep=%.1fs err=%s",
                    attempt, sleep, redact_secrets(str(exc)),
                )
                time.sleep(sleep)
            except Exception as exc:
                # Fatal (auth, bad request, etc.) — redact before surfacing.
                raise LLMError(redact_secrets(str(exc))) from exc
        else:
            raise LLMError(
                f"anthropic failed after {self._max_retries} retries: "
                + redact_secrets(str(last_exc))
            )

        text = _extract_text(raw)
        prompt_tokens = getattr(getattr(raw, "usage", None), "input_tokens", 0) or 0
        completion_tokens = getattr(getattr(raw, "usage", None), "output_tokens", 0) or 0
        return LLMResponse(
            text=text,
            model=self.model,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            metadata={
                "stop_reason": str(getattr(raw, "stop_reason", "") or ""),
                "response_id": str(getattr(raw, "id", "") or ""),
            },
        )

    # --- helpers ---------------------------------------------------------

    def _transient_errors(self) -> tuple[type[Exception], ...]:
        errs: list[type[Exception]] = []
        for name in (
            "APIConnectionError", "APITimeoutError",
            "RateLimitError", "InternalServerError",
        ):
            cls = getattr(self._anthropic, name, None)
            if isinstance(cls, type) and issubclass(cls, Exception):
                errs.append(cls)
        return tuple(errs) or (Exception,)


def _extract_text(raw: Any) -> str:
    """Concatenate text blocks from an anthropic Message response."""
    text_parts: list[str] = []
    for block in getattr(raw, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
    return "".join(text_parts)


# ------------------------------------------------------------------------
# Response parsing — extract first C++ fenced block
# ------------------------------------------------------------------------


_FENCE_RE = re.compile(
    r"```(?:c\+\+|cpp|CPP|C\+\+)?\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


def extract_cpp_block(text: str) -> str | None:
    """Return the first fenced ``cpp`` / ``c++`` block, or the first fenced
    block of any language. ``None`` if no fenced block is present."""
    # Prefer an explicitly-tagged cpp/c++ fence.
    for lang in ("cpp", "c++"):
        tagged = re.search(
            rf"```{re.escape(lang)}\s*\n(?P<body>.*?)\n```",
            text, re.DOTALL | re.IGNORECASE,
        )
        if tagged:
            return tagged.group("body")
    m = _FENCE_RE.search(text)
    if m:
        return m.group("body")
    return None


__all__ = [
    "LLMClient", "LLMResponse", "LLMError",
    "StubClient", "MockClient", "AnthropicClient",
    "extract_cpp_block", "redact_secrets",
]
