"""Passwords (scrypt) and compact HS256 JWTs. Zero heavy dependencies."""
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from .config import get_settings

# ---------- passwords ----------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False

# ---------- JWT (HS256) ----------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(claims: dict, ttl: Optional[int] = None) -> str:
    st = get_settings()
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = dict(claims, exp=int(time.time()) + (ttl or st.jwt_ttl_seconds))
    payload = _b64(json.dumps(body).encode())
    sig = hmac.new(st.jwt_secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


def read_token(token: str) -> Optional[dict]:
    try:
        header, payload, sig = token.split(".")
        expect = hmac.new(get_settings().jwt_secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(expect), sig):
            return None
        claims = json.loads(_unb64(payload))
        return None if claims.get("exp", 0) < time.time() else claims
    except Exception:
        return None

# ---------- API keys ----------

def new_api_key() -> tuple:
    """Returns (plaintext, sha256hex, prefix). Plaintext shown exactly once."""
    raw = "trk_" + base64.urlsafe_b64encode(os.urandom(24)).rstrip(b"=").decode()
    return raw, hashlib.sha256(raw.encode()).hexdigest(), raw[:10]


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
