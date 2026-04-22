#!/usr/bin/env python3
"""Stub LLM client (M7 scaffold; full impl in M10).

Defines the ``LLMClient`` protocol so ``scripts/evolve.py`` and tests can
depend on a stable surface before real Anthropic/Gemini adapters land in
M10. Nothing in this module contacts the network; the stub's ``generate``
raises ``NotImplementedError`` so accidental production use is loud.

Real M10 deliverables will add:
* ``AnthropicClient`` and ``GeminiClient`` implementations.
* Prompt templates under ``prompts/``.
* Secret handling via ``python-dotenv`` with redaction of any token that
  looks like an API key.
* Response parsing (extracts the first ```cpp ...``` fenced block) with
  banned-token rejection before the compiler sees it.
* Retry with exponential backoff (max 3 attempts).

Until then, tests can import ``LLMClient`` / ``LLMResponse`` / ``MockClient``
for type-checked mocking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    """A normalized response shape produced by every client."""
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


class LLMClient(Protocol):
    """Transport-agnostic protocol for prompt → response clients.

    Implementations in M10 will wrap provider SDKs. Tests in M10 will use
    ``MockClient`` with a queue of canned responses.
    """

    def generate(self, prompt: str, *, max_tokens: int = 4096) -> LLMResponse: ...


class StubClient:
    """Placeholder client that refuses to serve. M10 replaces this."""

    model: str = "stub"

    def generate(self, prompt: str, *, max_tokens: int = 4096) -> LLMResponse:
        raise NotImplementedError(
            "LLM client not implemented yet; planned for milestone M10. "
            "See IMPLEMENTATION_PLAN.md §12."
        )


class MockClient:
    """Deterministic test double; returns queued responses in order.

    Exposed from the module so tests outside of M10 (notably the
    orchestrator CLI tests in M7) can exercise plumbing without the
    SDK dependency.
    """

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


__all__ = ["LLMClient", "LLMResponse", "StubClient", "MockClient"]
