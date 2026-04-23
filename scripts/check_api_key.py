#!/usr/bin/env python3
"""Preflight validator for the Anthropic API key.

Reads ``ANTHROPIC_API_KEY`` (and optionally ``ANTHROPIC_MODEL``) from the
environment, issues a minimal ``/v1/messages`` request, and reports the
outcome via exit code and a short human-readable line.

The key is never printed, logged, or echoed — only the HTTP status, a
short classification, and (on success) the model name and token counts
are shown.

Exit codes:
    0  key is valid and the API responded successfully
    1  key missing or empty in the environment
    2  authentication failed (401 / 403)
    3  rate-limited (429) — key is valid but throttled
    4  other HTTP / transport error
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-3-5-haiku-20241022"
ANTHROPIC_VERSION = "2023-06-01"


def _build_ssl_context() -> ssl.SSLContext:
    """Return an SSL context using certifi's CA bundle when available.

    Falls back to the system default context if ``certifi`` is not
    installed. This sidesteps the common macOS issue where the bundled
    Python has no trusted roots configured.
    """
    try:
        import certifi  # type: ignore
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _classify(status: int) -> tuple[int, str]:
    if status == 200:
        return 0, "ok"
    if status in (401, 403):
        return 2, "auth-failed"
    if status == 429:
        return 3, "rate-limited"
    return 4, f"http-{status}"


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("ANTHROPIC_API_KEY: missing or empty", file=sys.stderr)
        return 1

    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    body = json.dumps(
        {
            "model": model,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "ping"}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )

    ctx = _build_ssl_context()
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            status = resp.status
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        code, label = _classify(exc.code)
        print(f"anthropic: {label} (HTTP {exc.code})", file=sys.stderr)
        return code
    except urllib.error.URLError as exc:
        print(f"anthropic: transport-error ({exc.reason})", file=sys.stderr)
        return 4
    except Exception as exc:  # noqa: BLE001
        print(f"anthropic: unexpected-error ({type(exc).__name__})", file=sys.stderr)
        return 4

    code, label = _classify(status)
    if code == 0:
        usage = payload.get("usage", {}) or {}
        print(
            f"anthropic: {label} "
            f"model={payload.get('model', model)} "
            f"in={usage.get('input_tokens', '?')} "
            f"out={usage.get('output_tokens', '?')}"
        )
    else:
        print(f"anthropic: {label} (HTTP {status})", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
