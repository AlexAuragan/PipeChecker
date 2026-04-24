"""Session-based authentication for the web UI.

Credentials are configured via environment variables:
  PIPECHECKER_WEB_USER          — username (default: admin)
  PIPECHECKER_WEB_PASSWORD_HASH — pbkdf2 hash in salt_hex:dk_hex format (see cli.py generate-web-password)
  PIPECHECKER_WEB_SECRET        — HMAC signing key for session cookies (auto-generated per process if unset)

Authentication is disabled when PIPECHECKER_WEB_PASSWORD_HASH is not set.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import Request

SESSION_COOKIE = "pc_session"
_SESSION_LIFETIME = 12 * 3600  # seconds

_secret: str | None = None


def _get_secret() -> str:
    global _secret
    if _secret is None:
        _secret = os.getenv("PIPECHECKER_WEB_SECRET") or secrets.token_hex(32)
    return _secret


def verify_credentials(username: str, password: str) -> bool:
    expected_user = os.getenv("PIPECHECKER_WEB_USER", "admin")
    stored = os.getenv("PIPECHECKER_WEB_PASSWORD_HASH")
    if stored is None or username != expected_user:
        return False
    try:
        salt_hex, dk_hex = stored.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def create_session_cookie(username: str) -> str:
    payload = base64.urlsafe_b64encode(f"{username}:{int(time.time())}".encode()).decode().rstrip("=")
    sig = hmac.new(_get_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_cookie(value: str | None) -> str | None:
    if not value:
        return None
    try:
        payload, sig = value.rsplit(".", 1)
    except ValueError:
        return None
    expected_sig = hmac.new(_get_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding).decode()
        username, ts_str = decoded.rsplit(":", 1)
        if time.time() - int(ts_str) > _SESSION_LIFETIME:
            return None
        return username
    except Exception:
        return None


class RequiresLoginException(Exception):
    def __init__(self, next_url: str = "/"):
        self.next_url = next_url


def require_web_auth(request: Request) -> str:
    """FastAPI dependency. Returns the logged-in username, or raises RequiresLoginException."""
    username = verify_session_cookie(request.cookies.get(SESSION_COOKIE))
    if username is None:
        raise RequiresLoginException(next_url=str(request.url.path))
    return username
