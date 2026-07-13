"""Provider-agnostic OAuth PKCE + loopback-callback helpers.

Shared by the Codex and Grok subscription logins (and any future loopback-PKCE
provider). Keeps the generic OAuth *shape* in one tested place; each provider
supplies its own constants, query params, and quirks.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple


class OAuthLoopbackError(RuntimeError):
    """Raised when the loopback login fails (timeout, state mismatch, error)."""


# ── Credential storage (shared by Codex + Grok token providers) ─────────────

def app_home() -> Path:
    """Directory for proxy-managed OAuth credential files (``$OAUTH_PROXY_HOME``)."""
    return Path(os.environ.get("OAUTH_PROXY_HOME") or (Path.home() / ".oauth-proxy"))


def read_json_credentials(path: Path) -> Optional[Dict]:
    """Read a persisted JSON credential record, or None if absent/unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None


def write_json_credentials(path: Path, record: Dict) -> None:
    """Persist a JSON credential record with owner-only permissions (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ── Token expiry (shared by Codex + Grok token providers) ───────────────────

def expires_at_ms(expires_in: Optional[float], *, now_ms: Optional[int] = None) -> Optional[int]:
    """Convert an OAuth ``expires_in`` (seconds) into an absolute epoch-ms expiry."""
    if not expires_in:
        return None
    base = now_ms if now_ms is not None else int(time.time() * 1000)
    return base + int(float(expires_in) * 1000)


def record_is_fresh(record: Optional[Dict], *, skew_ms: int) -> bool:
    """True if ``record``'s access token is valid and not within ``skew_ms`` of expiry."""
    if not record or not record.get("access_token"):
        return False
    exp = record.get("expires_at")
    if exp is None:
        return False  # unknown expiry — re-resolve to be safe
    return int(time.time() * 1000) < (int(exp) - skew_ms)


def b64url(raw: bytes) -> str:
    """URL-safe base64 without padding (PKCE + JWT segment encoding)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_pkce() -> Tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    import os

    verifier = b64url(os.urandom(64))[:128]
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def decode_jwt_segment(token: str, segment: int = 1) -> Dict:
    """Decode (without verifying) a JWT segment into a dict (default: payload)."""
    parts = token.split(".")
    if len(parts) <= segment:
        raise ValueError("not a JWT (missing segment)")
    seg = parts[segment]
    seg += "=" * (-len(seg) % 4)  # restore base64 padding
    return json.loads(base64.urlsafe_b64decode(seg.encode("ascii")))


class _CallbackHandler(BaseHTTPRequestHandler):
    # Set on the class by capture_redirect before serving (single-shot use).
    captured: Dict[str, str] = {}
    expected_path: str = "/callback"

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != type(self).expected_path:
            self.send_response(404)
            self.end_headers()
            return
        type(self).captured = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Login complete.</h2>"
            b"<p>You can close this tab and return to the terminal.</p></body></html>"
        )

    def log_message(self, *args) -> None:  # silence stdlib request logging
        return


def _bind(redirect_host: str, ports: Tuple[int, ...], path: str) -> Tuple[HTTPServer, str]:
    last: Optional[OSError] = None
    for port in ports:
        try:
            server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
        except OSError as exc:
            last = exc
            continue
        actual = server.server_address[1]
        return server, f"http://{redirect_host}:{actual}{path}"
    raise OAuthLoopbackError(f"could not bind the OAuth callback server on {ports}: {last}")


def capture_redirect(
    *,
    redirect_host: str,
    ports: Tuple[int, ...],
    path: str,
    build_authorize_url: Callable[[str], str],
    expected_state: str,
    open_browser: bool = True,
    timeout: float = 180.0,
) -> Tuple[str, str]:
    """Run the loopback half of an Authorization-Code+PKCE flow.

    Binds a localhost callback server (trying ``ports`` in order; pass ``0`` for
    an OS-assigned port), opens the browser to ``build_authorize_url(redirect_uri)``,
    waits for the redirect, validates ``state``, and returns
    ``(authorization_code, redirect_uri)``. Raises ``OAuthLoopbackError`` on
    timeout / state mismatch / provider error.
    """
    server, redirect_uri = _bind(redirect_host, ports, path)
    _CallbackHandler.expected_path = path
    _CallbackHandler.captured = {}

    url = build_authorize_url(redirect_uri)
    print(f"Opening browser for login:\n  {url}\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - headless/no browser
            print("(could not open a browser automatically — open the URL above manually)")

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()

    captured = _CallbackHandler.captured
    if not captured:
        raise OAuthLoopbackError("login timed out waiting for the OAuth redirect")
    if captured.get("state") != expected_state:
        raise OAuthLoopbackError("OAuth state mismatch (possible CSRF) — aborting")
    if "error" in captured:
        raise OAuthLoopbackError(f"authorization failed: {captured.get('error')}")
    code = captured.get("code")
    if not code:
        raise OAuthLoopbackError("no authorization code in the OAuth redirect")
    return code, redirect_uri
