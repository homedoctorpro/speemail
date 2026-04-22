from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from speemail.api.deps import get_db_dep, get_graph_dep
from speemail.auth.graph_auth import (
    AuthError,
    GraphClient,
    clear_token_cache,
    handle_auth_callback,
    start_auth_flow,
)
from speemail.services.user_identity import save_user_identity

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.get("/status", response_class=HTMLResponse)
def auth_status(request: Request, client: GraphClient = Depends(get_graph_dep)):
    authenticated = client.is_authenticated()
    display = ""
    if authenticated:
        try:
            me = client.get_me()
            display = me.get("displayName") or me.get("mail") or me.get("userPrincipalName", "")
        except Exception:
            authenticated = False

    if authenticated:
        html = (
            f'<span id="auth-badge" class="badge badge-green" title="{display}">'
            f'✓ {display[:20]}</span>'
        )
    else:
        html = (
            '<span id="auth-badge">'
            '<a href="/auth/login" class="badge badge-red">'
            'Sign in with Microsoft</a>'
            '</span>'
        )
    return HTMLResponse(html)


@router.get("/login")
def login(request: Request):
    """Redirect the user to Microsoft's OAuth sign-in page."""
    try:
        auth_url = start_auth_flow()
        return RedirectResponse(auth_url)
    except AuthError as exc:
        return request.app.state.templates.TemplateResponse(
            "auth_error.html", {"request": request, "error": str(exc)}
        )


@router.get("/callback")
def callback(request: Request, db: Session = Depends(get_db_dep)):
    """Microsoft redirects here after the user signs in."""
    try:
        handle_auth_callback(dict(request.query_params))
    except AuthError as exc:
        logger.error("OAuth callback error: %s", exc)
        return request.app.state.templates.TemplateResponse(
            "auth_error.html", {"request": request, "error": str(exc)}
        )

    # Persist the user's identity so classification can use it immediately
    try:
        from speemail.auth.graph_auth import get_graph_client
        me = get_graph_client().get_me()
        save_user_identity(db, me)
    except Exception as exc:
        logger.warning("Could not save user identity after login: %s", exc)

    return RedirectResponse("/", status_code=302)


@router.post("/logout")
def logout():
    clear_token_cache()
    return {"ok": True}
