"""Tests for the minimal .env loader and config."""
import os

from oauth_proxy.config import load_dotenv


def test_load_dotenv_sets_missing_var(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-xyz\n")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    load_dotenv(str(env))
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-xyz"


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("CLAUDE_CODE_OAUTH_TOKEN=from-file\n")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "from-real-env")
    load_dotenv(str(env))  # default override=False
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "from-real-env"


def test_override_true_replaces_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("X=fromfile\n")
    monkeypatch.setenv("X", "fromenv")
    load_dotenv(str(env), override=True)
    assert os.environ["X"] == "fromfile"


def test_handles_export_quotes_comments_blanks(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "\n"
        "# a comment\n"
        "export PROXY_PORT=9001\n"
        'DEFAULT_MODEL="claude-sonnet-4-6"\n'
        "EMPTY=\n"
        "  SPACED = value \n"
    )
    for k in ("PROXY_PORT", "DEFAULT_MODEL", "EMPTY", "SPACED"):
        monkeypatch.delenv(k, raising=False)
    load_dotenv(str(env))
    assert os.environ["PROXY_PORT"] == "9001"
    assert os.environ["DEFAULT_MODEL"] == "claude-sonnet-4-6"
    assert os.environ["EMPTY"] == ""
    assert os.environ["SPACED"] == "value"


def test_missing_file_is_noop(tmp_path):
    # Should not raise.
    load_dotenv(str(tmp_path / "does-not-exist.env"))
