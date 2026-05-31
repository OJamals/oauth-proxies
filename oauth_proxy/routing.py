"""Model-name -> upstream provider routing.

One request surface (OpenAI), several subscription backends. The provider is
chosen purely from the requested model name so any OpenAI client works by just
setting ``model``. Unknown names fall back to a configurable default.
"""
from __future__ import annotations

ANTHROPIC = "anthropic"
CODEX = "codex"
GROK = "grok"

_OPENAI_PREFIXES = ("gpt", "o1", "o3", "o4", "codex", "chatgpt")


def route_provider(model: str, *, default: str = ANTHROPIC) -> str:
    """Return the provider id for a requested model name."""
    n = (model or "").strip().lower()
    if n.startswith(("claude", "anthropic/")):
        return ANTHROPIC
    if n.startswith("grok"):
        return GROK
    if n.startswith(_OPENAI_PREFIXES) or "codex" in n:
        return CODEX
    return default
