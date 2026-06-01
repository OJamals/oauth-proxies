"""The vendored ``_paths`` shim must honor the same OAUTH_PROXY_HOME knob the
rest of the proxy uses (with a PROXY_HOME backward-compat fallback)."""
from __future__ import annotations

from pathlib import Path

from oauth_proxy._vendor import _paths


def test_app_home_honors_oauth_proxy_home(monkeypatch, tmp_path):
    monkeypatch.setenv("OAUTH_PROXY_HOME", str(tmp_path))
    monkeypatch.delenv("PROXY_HOME", raising=False)
    assert _paths._app_home() == tmp_path


def test_app_home_falls_back_to_legacy_proxy_home(monkeypatch, tmp_path):
    monkeypatch.delenv("OAUTH_PROXY_HOME", raising=False)
    monkeypatch.setenv("PROXY_HOME", str(tmp_path))
    assert _paths._app_home() == tmp_path


def test_app_home_defaults_to_dot_oauth_proxy(monkeypatch):
    monkeypatch.delenv("OAUTH_PROXY_HOME", raising=False)
    monkeypatch.delenv("PROXY_HOME", raising=False)
    assert _paths._app_home() == Path.home() / ".oauth-proxy"
