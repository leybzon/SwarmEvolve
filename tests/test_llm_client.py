"""Unit tests for ``scripts/llm_client.py``.

Deliberately avoid the live Anthropic SDK; the network-touching path is
exercised manually via ``scripts/evolve_once.py``. Here we cover:

* Fenced-block extraction (``extract_cpp_block``).
* Secret redaction.
* ``MockClient`` semantics (queue order, empty-queue error).
* ``AnthropicClient`` behavior: happy path, transient-error retry, and
  fatal-error path — all with a stub SDK so tests are offline-safe.
"""

from __future__ import annotations

import sys
import types as pytypes
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import llm_client

# -----------------------------------------------------------------------
# extract_cpp_block
# -----------------------------------------------------------------------


def test_extract_cpp_block_tagged():
    text = "prose\n```cpp\nint x = 1;\n```\nmore"
    assert llm_client.extract_cpp_block(text) == "int x = 1;"


def test_extract_cpp_block_cpp_case_insensitive():
    text = "```CPP\nint x = 1;\n```"
    assert llm_client.extract_cpp_block(text) == "int x = 1;"


def test_extract_cpp_block_cplusplus_tag():
    text = "```c++\nint x = 1;\n```"
    assert llm_client.extract_cpp_block(text) == "int x = 1;"


def test_extract_cpp_block_untagged_fence():
    text = "```\nint x = 1;\n```"
    assert llm_client.extract_cpp_block(text) == "int x = 1;"


def test_extract_cpp_block_prefers_cpp_fence_when_both_present():
    text = "```\nnot cpp\n```\nbefore\n```cpp\nint x = 1;\n```\nafter"
    assert llm_client.extract_cpp_block(text) == "int x = 1;"


def test_extract_cpp_block_none():
    assert llm_client.extract_cpp_block("just prose, no fences") is None


# -----------------------------------------------------------------------
# redact_secrets
# -----------------------------------------------------------------------


def test_redact_anthropic_key():
    msg = "error with sk-ant-abcdef1234567890_XYZ and more"
    out = llm_client.redact_secrets(msg)
    assert "sk-ant-" not in out
    assert "***REDACTED***" in out


def test_redact_api_key_label():
    msg = "api_key=abc123def456ghi789jklmnopqr"
    out = llm_client.redact_secrets(msg)
    assert "abc123" not in out
    assert "***REDACTED***" in out


def test_redact_leaves_benign_text():
    assert llm_client.redact_secrets("nothing to see here") == "nothing to see here"


# -----------------------------------------------------------------------
# MockClient
# -----------------------------------------------------------------------


def test_mock_client_queue_order():
    mc = llm_client.MockClient(
        [
            llm_client.LLMResponse(text="one", model="mock"),
            llm_client.LLMResponse(text="two", model="mock"),
        ]
    )
    assert mc.generate("p1").text == "one"
    assert mc.generate("p2").text == "two"
    assert mc.calls == ["p1", "p2"]


def test_mock_client_empty_raises():
    mc = llm_client.MockClient()
    with pytest.raises(RuntimeError):
        mc.generate("nope")


# -----------------------------------------------------------------------
# AnthropicClient — stub SDK so no network is touched
# -----------------------------------------------------------------------


class _FakeMessages:
    def __init__(self, raw):
        self._raw = raw
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._raw


class _FakeAnthropicSDK:
    """Minimal surface of the ``anthropic`` package used by the client."""

    class APIConnectionError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class InternalServerError(Exception):
        pass

    class Anthropic:
        def __init__(self, api_key=None, timeout=None):
            self.messages = _FakeMessages(_FakeAnthropicSDK._canned_raw())
            self.api_key = api_key
            self.timeout = timeout

    _canned_raw_factory = None  # type: ignore[var-annotated]

    @classmethod
    def _canned_raw(cls):
        if cls._canned_raw_factory is None:
            raise AssertionError("canned response factory not set")
        return cls._canned_raw_factory()


def _install_fake_sdk(monkeypatch, factory):
    fake_mod = pytypes.ModuleType("anthropic")
    for name in (
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "Anthropic",
    ):
        setattr(fake_mod, name, getattr(_FakeAnthropicSDK, name))
    _FakeAnthropicSDK._canned_raw_factory = factory
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)


def _raw(text: str, stop: str = "end_turn", in_tok: int = 3, out_tok: int = 5):
    """Build an object shaped like anthropic's Message response."""
    return pytypes.SimpleNamespace(
        id="msg_fake",
        stop_reason=stop,
        usage=pytypes.SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
        content=[pytypes.SimpleNamespace(type="text", text=text)],
    )


def test_anthropic_requires_key(monkeypatch):
    _install_fake_sdk(monkeypatch, lambda: _raw("hi"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm_client.LLMError):
        llm_client.AnthropicClient()


def test_anthropic_happy_path(monkeypatch):
    _install_fake_sdk(monkeypatch, lambda: _raw("hello world"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-xxxxxxxxxxxx")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test-123")
    c = llm_client.AnthropicClient()
    r = c.generate("prompt", max_tokens=200)
    assert r.text == "hello world"
    assert r.model == "claude-test-123"
    assert r.prompt_tokens == 3
    assert r.completion_tokens == 5
    assert r.metadata["response_id"] == "msg_fake"


def test_anthropic_retries_transient_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    def factory():
        # The factory returns a raw response; retries are driven by
        # raising inside messages.create, so we stub that directly.
        return _raw("ok")

    _install_fake_sdk(monkeypatch, factory)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-xxxxxxxxxxxx")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test-123")

    # Patch messages.create to fail the first 2 calls with a transient
    # error, then succeed.
    def flaky_create(**kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _FakeAnthropicSDK.RateLimitError("slow down")
        return _raw("after-retry")

    c = llm_client.AnthropicClient(max_retries=5)
    monkeypatch.setattr(c._client.messages, "create", flaky_create)
    monkeypatch.setattr(llm_client.time, "sleep", lambda _s: None)

    r = c.generate("prompt")
    assert r.text == "after-retry"
    assert attempts["n"] == 3


def test_anthropic_fatal_error_redacts(monkeypatch):
    _install_fake_sdk(monkeypatch, lambda: _raw("unused"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-xxxxxxxxxxxx")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test-123")
    c = llm_client.AnthropicClient()

    def boom(**kwargs):
        raise RuntimeError("auth failed; token=secret-value-zzzzzzzzzzzzzzzzzz")

    monkeypatch.setattr(c._client.messages, "create", boom)

    with pytest.raises(llm_client.LLMError) as excinfo:
        c.generate("prompt")
    # The fatal path must redact anything that looks like a key/token.
    assert "secret-value" not in str(excinfo.value)
