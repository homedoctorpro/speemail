from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from speemail.config import settings
from speemail.middleware.auth_middleware import _make_token

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str = ""):
    return request.app.state.templates.TemplateResponse(
        "login.html",
        {"request": request, "next": next, "error": error},
    )


@router.post("/login")
def login_submit(
    request: Request,
    password: str = Form(...),
    next: str = Form("/"),
):
    if settings.app_password and password != settings.app_password:
        return request.app.state.templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "Incorrect password."},
            status_code=401,
        )

    response = RedirectResponse(next or "/", status_code=302)
    response.set_cookie(
        "speemail_auth",
        _make_token(),
        max_age=86400 * 30,  # 30 days
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout-session")
def logout_session():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("speemail_auth")
    return response
