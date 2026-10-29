import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

bearer = HTTPBearer(auto_error=False)


def make_token() -> str:
    payload = {"sub": settings().demo_email, "exp": int(time.time()) + 7 * 86400}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = hmac.new(settings().app_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if credentials is None:
        raise HTTPException(401, "Authentication required")
    try:
        body, signature = credentials.credentials.split(".", 1)
        expected = hmac.new(
            settings().app_secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        if not secrets.compare_digest(signature, expected):
            raise ValueError("bad signature")
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload["sub"] != settings().demo_email or payload["exp"] < time.time():
            raise ValueError("expired")
        return payload["sub"]
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid token") from None


def check_login(email: str, password: str) -> bool:
    return secrets.compare_digest(email, settings().demo_email) and secrets.compare_digest(
        password, settings().demo_password
    )
