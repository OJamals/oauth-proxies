"""Tests for the transient-error retry helper and the token-refresh lock.

These guard two auth2api-inspired robustness features:
  * ``app._retry_upstream`` retries only transient (429/5xx) upstream failures.
  * ``CodexTokenProvider`` / ``GrokTokenProvider`` serialize concurrent refreshes
    so a rotating refresh token is spent exactly once.
"""
from __future__ import annotations

import threading
import time

import pytest

from oauth_proxy import app as app_mod
from oauth_proxy import codex_auth, grok_auth


class _HTTPError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _future_ms(seconds: int = 3600) -> int:
    return int(time.time() * 1000) + seconds * 1000


# ── _retry_upstream ──────────────────────────────────────────────────────────

def test_retry_returns_immediately_on_success():
    calls = []

    def call():
        calls.append(1)
        return "ok"

    assert app_mod._retry_upstream(call, provider="test", sleep=lambda _: None) == "ok"
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retry_recovers_after_transient_failure(status):
    seq = [_HTTPError(status), _HTTPError(status), "ok"]

    def call():
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    assert app_mod._retry_upstream(call, provider="test", sleep=lambda _: None) == "ok"
    assert seq == []  # all three attempts consumed


def test_retry_gives_up_after_max_attempts():
    calls = []

    def call():
        calls.append(1)
        raise _HTTPError(503)

    with pytest.raises(_HTTPError):
        app_mod._retry_upstream(call, provider="test", sleep=lambda _: None)
    assert len(calls) == app_mod._MAX_RETRIES + 1  # initial + retries


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_retry_does_not_retry_client_or_auth_errors(status):
    calls = []

    def call():
        calls.append(1)
        raise _HTTPError(status)

    with pytest.raises(_HTTPError):
        app_mod._retry_upstream(call, provider="test", sleep=lambda _: None)
    assert len(calls) == 1  # no retry


def test_retry_does_not_retry_non_http_exception():
    calls = []

    def call():
        calls.append(1)
        raise ValueError("boom")  # no status_code attribute

    with pytest.raises(ValueError):
        app_mod._retry_upstream(call, provider="test", sleep=lambda _: None)
    assert len(calls) == 1


# ── Refresh lock ─────────────────────────────────────────────────────────────

def test_codex_concurrent_get_token_refreshes_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OAUTH_PROXY_HOME", str(tmp_path))
    codex_auth.write_credentials(
        {"access_token": "stale", "refresh_token": "r1", "expires_at": 1}  # long expired
    )

    refresh_calls = []

    def slow_refresh(refresh_token, *, timeout):
        refresh_calls.append(refresh_token)
        time.sleep(0.05)  # widen the race window
        return {"access_token": "new", "refresh_token": "r2", "expires_in": 3600}

    monkeypatch.setattr(codex_auth, "_refresh", slow_refresh)

    tp = codex_auth.CodexTokenProvider()
    results: list = []

    def worker():
        results.append(tp.get_token())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The rotating refresh token was spent exactly once despite 8 racing callers.
    assert refresh_calls == ["r1"]
    assert results == ["new"] * 8


def test_grok_concurrent_get_token_refreshes_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OAUTH_PROXY_HOME", str(tmp_path))
    grok_auth.write_credentials(
        {"access_token": "stale", "refresh_token": "r1", "expires_at": 1,
         "token_endpoint": "https://auth.x.ai/oauth2/token"}
    )

    refresh_calls = []

    def slow_refresh(token_endpoint, refresh_token, *, timeout):
        refresh_calls.append(refresh_token)
        time.sleep(0.05)
        return {"access_token": "new", "refresh_token": "r2", "expires_in": 3600}

    monkeypatch.setattr(grok_auth, "_refresh", slow_refresh)

    tp = grok_auth.GrokTokenProvider()
    results: list = []

    def worker():
        results.append(tp.get_token())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert refresh_calls == ["r1"]
    assert results == ["new"] * 8
