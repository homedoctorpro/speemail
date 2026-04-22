import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from speemail.api.deps import get_db_dep, get_graph_dep
from speemail.auth.graph_auth import GraphClient
from speemail.services import ai_chat

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _get_user_name(client: GraphClient) -> str:
    try:
        me = client.get_me()
        return me.get("displayName") or me.get("userPrincipalName") or "User"
    except Exception:
        return "User"


@router.get("/api/v1/chat/messages", response_class=HTMLResponse)
def get_chat_messages(request: Request, db: Session = Depends(get_db_dep)):
    messages = ai_chat.get_history(db)
    return request.app.state.templates.TemplateResponse(
        "partials/_chat_messages.html",
        {"request": request, "messages": messages},
    )


@router.post("/api/v1/chat", response_class=HTMLResponse)
def send_chat_message(
    request: Request,
    db: Session = Depends(get_db_dep),
    client: GraphClient = Depends(get_graph_dep),
    message: str = Form(...),
):
    message = message.strip()
    if not message:
        return Response(status_code=204)

    user_name = _get_user_name(client)

    try:
        reply = ai_chat.chat(db, client, user_name, message)
    except Exception:
        logger.exception("Chat AI error")
        reply = "Sorry, I ran into an error. Please try again."

    return request.app.state.templates.TemplateResponse(
        "partials/_chat_new_messages.html",
        {"request": request, "user_message": message, "assistant_message": reply},
    )


@router.delete("/api/v1/chat/history")
def clear_chat_history(db: Session = Depends(get_db_dep)):
    ai_chat.clear_history(db)
    return Response(status_code=200, content="")
