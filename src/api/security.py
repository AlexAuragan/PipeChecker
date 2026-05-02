import hashlib
import hmac
import os

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _verify_key(key: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", key.encode(), bytes.fromhex(salt_hex), 100_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


async def require_api_key(key: str = Security(api_key_header)):
    stored = os.getenv("PIPECHECKER_API_KEY_HASH")
    if not key or not stored or not _verify_key(key, stored):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
