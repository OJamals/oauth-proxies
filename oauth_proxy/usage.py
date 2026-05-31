"""In-process per-provider usage / rate-limit snapshot (best-effort).

Codex exposes a dedicated usage endpoint (pulled on demand). Grok and Claude
only emit ``x-ratelimit-*`` / ``anthropic-ratelimit-*`` headers on real
responses, so we passively capture those here as traffic flows — no extra
billable requests. ``GET /usage`` reads this snapshot.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

_store: Dict[str, Dict[str, Any]] = {}


def record(provider: str, data: Dict[str, Any]) -> None:
    snapshot = dict(data)
    snapshot["observed_at"] = int(time.time())
    _store[provider] = snapshot


def get(provider: str) -> Optional[Dict[str, Any]]:
    return _store.get(provider)


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def record_ratelimit_headers(provider: str, headers: Any) -> None:
    """Capture standard ``x-ratelimit-*`` headers (xAI/OpenAI style), if present."""
    rl = {
        "limit_requests": _to_int(headers.get("x-ratelimit-limit-requests")),
        "remaining_requests": _to_int(headers.get("x-ratelimit-remaining-requests")),
        "limit_tokens": _to_int(headers.get("x-ratelimit-limit-tokens")),
        "remaining_tokens": _to_int(headers.get("x-ratelimit-remaining-tokens")),
        "reset_requests": headers.get("x-ratelimit-reset-requests"),
        "reset_tokens": headers.get("x-ratelimit-reset-tokens"),
    }
    if any(v is not None for v in rl.values()):
        record(provider, {"rate_limit": {k: v for k, v in rl.items() if v is not None}})
