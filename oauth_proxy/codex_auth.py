"""Codex (ChatGPT subscription) OAuth: PKCE login, token store, refresh.

This proxy runs its OWN OAuth flow against OpenAI's auth server using the
*official public Codex CLI client identity* — the ChatGPT subscription backend
only honors that client, so we reuse it (we do not, and cannot, register a
private app). We do NOT read the Codex CLI's stored credentials; this module
mints and persists its own token bundle under ``~/.oauth-proxy/.codex_oauth.json``.

All constants below were read from the official ``openai/codex`` Rust source
(``codex-rs/login`` + ``codex-rs/model-provider-info``) and adversarially
verified; see the design spec for provenance. They are public client values,
not secrets.

CONTRACT (app.py and tests depend on these):

    class TokenError(RuntimeError): ...

    class CodexTokenProvider:
        def __init__(self, *, timeout: float = 900.0) -> None: ...
        def get_token(self) -> str: ...        # resolves + refreshes; raises TokenError
        def account_id(self) -> Optional[str]: ...
        def headers(self) -> Dict[str, str]: ...  # Authorization + ChatGPT-Account-ID + originator

    def login(*, open_browser: bool = True, timeout: float = 180.0) -> dict: ...

Pure helpers (``_generate_pkce``, ``_build_authorize_url``,
``_account_id_from_id_token``) take no I/O and are unit tested directly.
PKCE generation, JWT-segment decoding, and the loopback callback server are
shared with Grok via ``oauth_pkce``.
"""
from __future__ import annotations

import json
import secrets
import threading
import urllib.parse
from pathlib import Path
from typing import Dict, Optional

import httpx

from oauth_proxy import oauth_pkce

# ── Verified public Codex CLI OAuth constants (openai/codex, main) ──────────
# codex-rs/login/src/auth/manager.rs: CLIENT_ID
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
# codex-rs/login/src/server.rs: DEFAULT_ISSUER
_ISSUER = "https://auth.openai.com"
AUTHORIZE_ENDPOINT = f"{_ISSUER}/oauth/authorize"
TOKEN_ENDPOINT = f"{_ISSUER}/oauth/token"
# codex-rs/login/src/server.rs: DEFAULT_PORT (1455) + FALLBACK_PORT (1457),
# redirect path "/auth/callback". Both ports are in OpenAI's redirect allowlist.
_REDIRECT_PORTS = (1455, 1457)
_REDIRECT_PATH = "/auth/callback"
# codex-rs/login/src/server.rs authorize query: scope + flow flags.
_SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"
# codex-rs/login/src/auth/default_client.rs: DEFAULT_ORIGINATOR
ORIGINATOR = "codex_cli_rs"

# ── Verified Codex Responses backend (ChatGPT-subscription mode) ────────────
# codex-rs/model-provider-info: CHATGPT_CODEX_BASE_URL + "/responses".
RESPONSES_BASE_URL = "https://chatgpt.com/backend-api/codex"
RESPONSES_ENDPOINT = f"{RESPONSES_BASE_URL}/responses"
# Live model allowlist for the logged-in ChatGPT account (requires a
# ``client_version`` query param). NOTE: this listing is VERSION-GATED — an old
# client_version under-reports (e.g. 0.20.0 returns only gpt-5.2), while a high
# one returns the full current set (gpt-5.5/5.4/5.4-mini/5.3-codex/5.2). The
# inference endpoint accepts all of them regardless, so we send a high version
# to surface everything the account can actually use.
MODELS_ENDPOINT = f"{RESPONSES_BASE_URL}/models"
# Free usage endpoint: plan, rate-limit windows (5h primary / weekly secondary),
# credits. No inference cost.
USAGE_ENDPOINT = f"{RESPONSES_BASE_URL}/usage"
CLIENT_VERSION = "1.0.0"

# id_token JWT claim namespace -> chatgpt_account_id (codex-rs/login/token_data.rs)
_AUTH_CLAIM = "https://api.openai.com/auth"

# Re-resolve a cached token this many ms before its known expiry.
_EXPIRY_SKEW_MS = 60_000


class TokenError(RuntimeError):
    """Raised when no usable Codex OAuth subscription token can be resolved."""


# ── Pure helpers (no I/O — unit tested directly) ────────────────────────────

# Shared PKCE generator (identical S256 logic Grok's login uses).
_generate_pkce = oauth_pkce.generate_pkce


def _build_authorize_url(*, redirect_uri: str, code_challenge: str, state: str) -> str:
    """Build the OpenAI authorize URL with the exact Codex CLI query params."""
    params = {
        "response_type": "code",
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": _SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": ORIGINATOR,
    }
    return AUTHORIZE_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _account_id_from_id_token(id_token: Optional[str]) -> Optional[str]:
    """Extract ``chatgpt_account_id`` from the id_token's auth claim, if present."""
    if not id_token:
        return None
    try:
        claims = oauth_pkce.decode_jwt_segment(id_token)
    except (ValueError, json.JSONDecodeError):
        return None
    auth = claims.get(_AUTH_CLAIM)
    if isinstance(auth, dict):
        acc = auth.get("chatgpt_account_id")
        return acc if isinstance(acc, str) and acc else None
    return None


def _record_from_token_response(
    data: Dict, *, prev: Optional[Dict] = None, now_ms: Optional[int] = None
) -> Dict:
    """Build a stored credential record from a token-endpoint JSON response.

    Refresh responses may omit ``refresh_token`` (reuse the old one) and
    ``id_token`` (reuse the old account id). ``prev`` carries those forward.
    """
    prev = prev or {}
    id_token = data.get("id_token") or prev.get("id_token")
    refresh_token = data.get("refresh_token") or prev.get("refresh_token")
    account_id = _account_id_from_id_token(id_token) or prev.get("account_id")
    return {
        "access_token": data.get("access_token"),
        "refresh_token": refresh_token,
        "id_token": id_token,
        "account_id": account_id,
        "expires_at": oauth_pkce.expires_at_ms(data.get("expires_in"), now_ms=now_ms),
        "token_type": data.get("token_type") or "Bearer",
    }


# ── Storage ─────────────────────────────────────────────────────────────────

def _store_path() -> Path:
    return oauth_pkce.app_home() / ".codex_oauth.json"


def read_credentials() -> Optional[Dict]:
    """Read the persisted Codex OAuth record, or None if absent/unreadable."""
    return oauth_pkce.read_json_credentials(_store_path())


def write_credentials(record: Dict) -> None:
    """Persist the Codex OAuth record with owner-only permissions (0600)."""
    oauth_pkce.write_json_credentials(_store_path(), record)


# ── Token-endpoint I/O ───────────────────────────────────────────────────────

def _post_token(form: Dict[str, str], *, timeout: float) -> Dict:
    resp = httpx.post(
        TOKEN_ENDPOINT,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise TokenError(f"token endpoint returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _exchange_code(*, code: str, verifier: str, redirect_uri: str, timeout: float) -> Dict:
    return _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": OAUTH_CLIENT_ID,
            "code_verifier": verifier,
        },
        timeout=timeout,
    )


def _refresh(refresh_token: str, *, timeout: float) -> Dict:
    return _post_token(
        {
            "grant_type": "refresh_token",
            "client_id": OAUTH_CLIENT_ID,
            "refresh_token": refresh_token,
        },
        timeout=timeout,
    )


# ── Loopback PKCE login ──────────────────────────────────────────────────────

def login(*, open_browser: bool = True, timeout: float = 180.0) -> Dict:
    """Run the interactive PKCE loopback login and persist the token bundle.

    Opens the browser to OpenAI's consent screen, captures the redirect on a
    local loopback server, exchanges the code, and writes the credential record.
    Returns the stored record. Raises ``TokenError`` on failure/timeout.
    """
    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    try:
        code, redirect_uri = oauth_pkce.capture_redirect(
            redirect_host="localhost",
            ports=_REDIRECT_PORTS,
            path=_REDIRECT_PATH,
            build_authorize_url=lambda ru: _build_authorize_url(
                redirect_uri=ru, code_challenge=challenge, state=state
            ),
            expected_state=state,
            open_browser=open_browser,
            timeout=timeout,
        )
    except oauth_pkce.OAuthLoopbackError as exc:
        raise TokenError(str(exc))

    token_resp = _exchange_code(
        code=code, verifier=verifier, redirect_uri=redirect_uri, timeout=timeout
    )
    record = _record_from_token_response(token_resp)
    if not record.get("access_token"):
        raise TokenError("token exchange did not return an access_token")
    write_credentials(record)
    return record


# ── Token provider (resolve + refresh + cache) ───────────────────────────────

class CodexTokenProvider:
    def __init__(self, *, timeout: float = 900.0) -> None:
        self.timeout = timeout
        self._record: Optional[Dict] = None
        # Serialize refresh: the Codex backend ROTATES the refresh token on every
        # refresh, so two concurrent refreshes would each invalidate the other's
        # token and can get the account revoked. FastAPI runs endpoints in worker
        # threads sharing this provider, so a lock is required.
        self._lock = threading.Lock()

    def _fresh(self, record: Optional[Dict]) -> bool:
        return oauth_pkce.record_is_fresh(record, skew_ms=_EXPIRY_SKEW_MS)

    def get_token(self) -> str:
        """Resolve a valid Codex access token, refreshing if needed.

        Raises ``TokenError`` (advising ``oauth-proxy login codex``) when no
        usable credential exists.
        """
        if self._fresh(self._record):
            return self._record["access_token"]  # type: ignore[index]

        with self._lock:
            # Re-check under the lock: another thread may have refreshed while we
            # waited, so we serve its result instead of refreshing again.
            if self._fresh(self._record):
                return self._record["access_token"]  # type: ignore[index]

            record = self._record or read_credentials()
            if not record:
                raise TokenError(
                    "No Codex OAuth token found. Run `oauth-proxy login codex` to "
                    "authorize with your ChatGPT subscription."
                )

            if self._fresh(record):
                self._record = record
                return record["access_token"]

            refresh_token = record.get("refresh_token")
            if not refresh_token:
                raise TokenError(
                    "Codex OAuth token is expired and no refresh token is stored. "
                    "Run `oauth-proxy login codex` again."
                )
            refreshed = _record_from_token_response(
                _refresh(refresh_token, timeout=self.timeout), prev=record
            )
            if not refreshed.get("access_token"):
                raise TokenError(
                    "Codex token refresh failed. Run `oauth-proxy login codex` again."
                )
            write_credentials(refreshed)
            self._record = refreshed
            return refreshed["access_token"]

    def is_logged_in(self) -> bool:
        """Cheap, local check: is a stored Codex credential present? (no network)"""
        record = self._record or read_credentials()
        return bool(record and record.get("access_token"))

    def account_id(self) -> Optional[str]:
        record = self._record or read_credentials() or {}
        return record.get("account_id") or _account_id_from_id_token(record.get("id_token"))

    def headers(self) -> Dict[str, str]:
        """Build the request headers for the Codex Responses backend."""
        h = {
            "Authorization": f"Bearer {self.get_token()}",
            "originator": ORIGINATOR,
        }
        acc = self.account_id()
        if acc:
            h["ChatGPT-Account-ID"] = acc
        return h
