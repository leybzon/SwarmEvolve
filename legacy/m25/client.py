"""Model-client interface for the frozen M25 loop.

M25 built ``llm_client.AnthropicClient`` objects inside the loop. The
snapshot instead receives its planner and coder clients from the caller
(execution change 3 in CHANGES.md), so arm A1 can use the same hardened
client, models, effort and max_tokens as every other arm.

``LLMResponse``, ``LLMClient`` and ``MockClient`` are copied unchanged from
``scripts/llm_client.py`` at 7e539a4; they are the only parts of that
module the loop used. ``RecordingClient`` is new.

What M25 sent per call (7e539a4 ``AnthropicClient.generate``): one user
message holding the prompt, no system prompt, no temperature (API default),
and ``max_tokens=4096``. The loop still passes ``max_tokens=4096`` to
``generate``; an injected client may honour it or replace it with the
arm-wide setting, and ``RecordingClient`` records the value asked for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

# ------------------------------------------------------------------------
# Response / protocol shapes (copied from 7e539a4 scripts/llm_client.py)
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
# Injection
# ------------------------------------------------------------------------

# ``factory(role, model)`` → client, with role "planner" or "coder". The
# CLI's ``--client-factory module:attr`` names one of these.
ClientFactory = Callable[[str, str], LLMClient]


@dataclass(frozen=True)
class RecordedCall:
    """One ``generate`` call as the loop made it."""

    prompt: str
    max_tokens: int


class RecordingClient:
    """Mock client that keeps every call, for characterising the loop.

    Returns queued response texts in order and raises when the queue runs
    dry, so a scenario that makes more calls than expected fails loudly.
    """

    def __init__(self, texts: list[str], model: str):
        self._queue = list(texts)
        self.model = model
        self.calls: list[RecordedCall] = []

    def generate(self, prompt: str, *, max_tokens: int = 4096) -> LLMResponse:
        self.calls.append(RecordedCall(prompt=prompt, max_tokens=max_tokens))
        if not self._queue:
            raise RuntimeError(f"RecordingClient {self.model}: no response queued")
        return LLMResponse(
            text=self._queue.pop(0),
            model=self.model,
            prompt_tokens=0,
            completion_tokens=0,
        )


__all__ = [
    "ClientFactory",
    "LLMClient",
    "LLMResponse",
    "MockClient",
    "RecordedCall",
    "RecordingClient",
]
