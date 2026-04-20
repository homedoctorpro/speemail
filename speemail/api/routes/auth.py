from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from speemail.api.deps import get_graph_dep
from speemail.auth.graph_auth import (
    GraphClient,
    clear_token_cache,
    get_device_flow_state,
    initiate_device_flow_async,
)

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
            '<a href="/auth/device-flow" class="badge badge-red" '
            'title="Click to connect your Microsoft account">⚠ Connect account</a>'
            '</span>'
        )
    return HTMLResponse(html)


@router.get("/device-flow", response_class=HTMLResponse)
def device_flow_page(request: Request):
    try:
        flow = initiate_device_flow_async()
    except Exception as exc:
        return request.app.state.templates.TemplateResponse(
            "device_flow.html",
            {"request": request, "error": str(exc)},
        )
    return request.app.state.templates.TemplateResponse(
        "device_flow.html",
        {
            "request": request,
            "user_code": flow["user_code"],
            "verification_uri": flow["verification_uri"],
            "error": None,
        },
    )


@router.get("/device-flow/status", response_class=HTMLResponse)
def device_flow_status(request: Request):
    state = get_device_flow_state()
    if state["completed"]:
        return HTMLResponse(
            '<div id="flow-status" class="badge badge-green">✓ Connected! '
            '<a href="/" style="color:inherit">Return to app →</a></div>'
        )
    if state["error"]:
        return HTMLResponse(
            f'<div id="flow-status" class="badge badge-red">Error: {state["error"]}</div>'
        )
    return HTMLResponse(
        '<div id="flow-status" class="badge badge-gray" '
        'hx-get="/auth/device-flow/status" hx-trigger="every 3s" hx-swap="outerHTML">'
        'Waiting for sign-in…</div>'
    )


@router.post("/logout")
def logout():
    clear_token_cache()
    return {"ok": True}
