"""Password and signed cookie helpers using only Python's standard library."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets

ITERATIONS = 310_000
SECRET_KEY = os.environ.get("APP_SECRET") or secrets.token_hex(32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, saved: str) -> bool:
    try:
        iterations, salt_text, digest_text = saved.split("$", 2)
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def encode_session(session: dict) -> str:
    raw = json.dumps(session, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    signature = hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + signature


def decode_session(value: str | None) -> dict:
    if not value or "." not in value:
        return {}
    body, signature = value.rsplit(".", 1)
    expected = hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return {}
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}

