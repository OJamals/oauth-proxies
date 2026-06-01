"""Shared test fixtures."""
import pytest

from oauth_proxy import usage as _usage


@pytest.fixture(autouse=True)
def _isolate_oauth_home(tmp_path_factory, monkeypatch):
    """Point OAUTH_PROXY_HOME at an empty temp dir for every test, and reset the
    in-process usage snapshot.

    Keeps tests hermetic: real token providers never read (or refresh against)
    the developer's actual ~/.oauth-proxy credentials, and /v1/models never
    makes a live network call from a real stored token. Tests that need a
    populated home override this with their own ``monkeypatch.setenv``.
    """
    monkeypatch.setenv("OAUTH_PROXY_HOME", str(tmp_path_factory.mktemp("oauth-home")))
    # Drop any credential env vars the developer may have exported so the
    # env-injection / env-first code paths see a clean slate; tests that exercise
    # them set the vars explicitly with their own ``monkeypatch.setenv``.
    for _var in (
        "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_TOKEN", "ANTHROPIC_API_KEY",
        "CODEX_REFRESH_TOKEN", "CODEX_ACCESS_TOKEN", "CODEX_ACCOUNT_ID", "CODEX_ID_TOKEN",
        "GROK_REFRESH_TOKEN", "GROK_ACCESS_TOKEN", "GROK_TOKEN_ENDPOINT",
    ):
        monkeypatch.delenv(_var, raising=False)
    _usage._store.clear()
    yield
    _usage._store.clear()
