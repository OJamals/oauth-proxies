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
    _usage._store.clear()
    yield
    _usage._store.clear()
