from __future__ import annotations

import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from speemail.config import settings
from speemail.middleware.auth_middleware import _make_token

router = APIRouter(tags=["auth"])


def _safe_next(value: str | None) -> str:
    """Only allow same-origin relative paths — blocks //evil.com and https://evil.com."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str = ""):
    return request.app.state.templates.TemplateResponse(
        "login.html",
        {"request": request, "next": _safe_next(next), "error": error},
    )


@router.post("/login")
def login_submit(
    request: Request,
    password: str = Form(...),
    next: str = Form("/"),
):
    next_url = _safe_next(next)
    expected = settings.app_password or ""
    if expected and not hmac.compare_digest(password, expected):
        return request.app.state.templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next_url, "error": "Incorrect password."},
            status_code=401,
        )

    response = RedirectResponse(next_url, status_code=302)
    response.set_cookie(
        "speemail_auth",
        _make_token(),
        max_age=86400 * 30,  # 30 days
        httponly=True,
        samesite="lax",
        secure=settings.server_mode,
    )
    return response


@router.post("/logout-session")
def logout_session():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("speemail_auth")
    return response
