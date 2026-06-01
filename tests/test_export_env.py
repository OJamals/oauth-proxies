"""`oauth-proxy export-env` dumps the portable credentials (Claude setup-token +
Codex/Grok refresh tokens) as KEY=value lines for migrating to another host or
Docker. Tests cover the pure line-builder so no .env / network is touched."""
from __future__ import annotations

from oauth_proxy import app, codex_auth, grok_auth


def test_export_env_lines_includes_all_when_present(monkeypatch):
    # conftest clears credential env vars + points OAUTH_PROXY_HOME at temp.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-claude")
    codex_auth.write_credentials(
        {"access_token": "a", "refresh_token": "crt", "account_id": "acc1"}
    )
    grok_auth.write_credentials(
        {"access_token": "a", "refresh_token": "grt",
         "token_endpoint": "https://custom.example/oauth2/token"}  # non-default
    )

    lines = app._export_env_lines()

    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-claude" in lines
    assert "CODEX_REFRESH_TOKEN=crt" in lines
    assert "CODEX_ACCOUNT_ID=acc1" in lines
    assert "GROK_REFRESH_TOKEN=grt" in lines
    assert "GROK_TOKEN_ENDPOINT=https://custom.example/oauth2/token" in lines


def test_export_env_lines_comments_when_absent():
    # Nothing set anywhere -> only guidance comments, no secret KEY=value lines.
    lines = app._export_env_lines()
    assert any(l.startswith("# CLAUDE_CODE_OAUTH_TOKEN") for l in lines)
    assert any(l.startswith("# CODEX_REFRESH_TOKEN") for l in lines)
    assert any(l.startswith("# GROK_REFRESH_TOKEN") for l in lines)
    assert not any("=" in l and not l.startswith("#") for l in lines)


def test_export_env_omits_default_grok_endpoint():
    grok_auth.write_credentials(
        {"refresh_token": "grt", "token_endpoint": grok_auth._TOKEN_FALLBACK}
    )
    lines = app._export_env_lines()
    assert "GROK_REFRESH_TOKEN=grt" in lines
    assert not any(l.startswith("GROK_TOKEN_ENDPOINT=") for l in lines)
