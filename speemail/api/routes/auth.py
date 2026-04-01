from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from speemail.auth.graph_auth import GraphClient, clear_token_cache
from speemail.api.deps import get_graph_dep

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
            '<span id="auth-badge" class="badge badge-red" '
            'title="Not connected — run python -m speemail in terminal to authenticate">'
            '⚠ Not connected</span>'
        )
    return HTMLResponse(html)


@router.post("/logout")
def logout():
    clear_token_cache()
    return {"ok": True}
