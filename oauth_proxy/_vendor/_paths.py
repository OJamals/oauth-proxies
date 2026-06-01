"""Shim providing the local-paths helper the vendored adapter imports.

The vendored adapter does ``from _paths import _app_home`` at module load to
compute ``_PROXY_OAUTH_FILE`` (the on-disk location for a locally-run PKCE
OAuth flow). This proxy reuses Claude Code's own credential store and never
exercises that PKCE code path, so the directory is effectively unused — but
the import must resolve at load time.
"""
import os
from pathlib import Path


def _app_home() -> Path:
    """Return the directory used for proxy-managed credential files.

    Uses ``OAUTH_PROXY_HOME`` — the single home-dir knob shared with
    ``codex_auth``/``grok_auth`` — falling back to the legacy ``PROXY_HOME``
    name, then to ``~/.oauth-proxy``.
    """
    base = os.environ.get("OAUTH_PROXY_HOME") or os.environ.get("PROXY_HOME")
    return Path(base) if base else Path.home() / ".oauth-proxy"
