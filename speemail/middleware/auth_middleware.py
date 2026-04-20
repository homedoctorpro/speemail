from __future__ import annotations

import hashlib
import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from speemail.config import settings

# Paths that don't require a password
_EXEMPT = ("/login", "/static", "/auth/device-flow", "/auth/status", "/auth/logout")


def _make_token() -> str:
    return hmac.new(
        settings.app_password.encode(),
        b"speemail-session",
        hashlib.sha256,
    ).hexdigest()


def verify_cookie(request: Request) -> bool:
    if not settings.app_password:
        return True
    token = request.cookies.get("speemail_auth", "")
    return hmac.compare_digest(token, _make_token())


class PasswordAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.app_password:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT):
            return await call_next(request)

        if not verify_cookie(request):
            return RedirectResponse(f"/login?next={path}", status_code=302)

        return await call_next(request)
