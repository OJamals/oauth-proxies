"""Tests for app._error_message — normalize upstream error bodies to a message.

The three backends return errors in different JSON dialects; the proxy pulls a
clean message string out rather than forwarding the raw JSON blob to the client.
"""
from __future__ import annotations

import pytest

from oauth_proxy import app as app_mod


class _HTTPError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def _msg(raw: str) -> str:
    return app_mod._error_message(_HTTPError(raw))


def test_anthropic_openai_error_dialect():
    assert _msg('{"error": {"message": "rate limited", "type": "rate_limit_error"}}') == "rate limited"


def test_bare_error_string_dialect():
    assert _msg('{"error": "invalid model"}') == "invalid model"


def test_codex_detail_dialect():
    assert _msg('{"detail": "You are not entitled to this model"}') == "You are not entitled to this model"


def test_top_level_message_dialect():
    assert _msg('{"message": "bad request"}') == "bad request"


def test_non_string_detail_is_json_encoded():
    # FastAPI/Starlette validation errors put a list under "detail".
    out = _msg('{"detail": [{"loc": ["body"], "msg": "field required"}]}')
    assert "field required" in out


def test_plain_text_passthrough():
    # Our own TokenError messages and HTTP fallbacks aren't JSON — unchanged.
    assert _msg("Run `oauth-proxy login codex` to authorize.") == "Run `oauth-proxy login codex` to authorize."
    assert _msg("HTTP 502") == "HTTP 502"


def test_message_embedded_after_prefix():
    # Some SDKs prefix the body with a status line before the JSON object.
    assert _msg('Error code: 429 - {"error": {"message": "slow down"}}') == "slow down"


def test_malformed_json_passthrough():
    assert _msg('{"error": {"message": ') == '{"error": {"message": '


def test_empty_message_falls_back_to_raw():
    raw = '{"error": {"message": ""}}'
    assert _msg(raw) == raw  # empty message isn't useful; keep the raw body
