"""
Microsoft Graph API authentication via MSAL confidential client + OAuth auth-code flow.

First run: user clicks "Sign in with Microsoft" → redirected to Microsoft → redirected back.
Subsequent runs: MSAL silently refreshes the access token using the cached refresh token.
Refresh tokens renew on every use (every 15-min poll), so they never expire in practice.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
import msal

from speemail.config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Holds the in-progress OAuth flow dict between the /auth/login redirect and /auth/callback
_pending_flow: dict | None = None


class AuthError(Exception):
    pass


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    path = settings.token_cache_path
    if path.exists():
        cache.deserialize(path.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        settings.token_cache_path.write_text(cache.serialize(), encoding="utf-8")


def _build_app(cache: msal.SerializableTokenCache) -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.azure_client_id,
        client_credential=settings.azure_client_secret,
        authority=f"https://login.microsoftonline.com/{settings.azure_tenant_id}",
        token_cache=cache,
        validate_authority=False,
    )


def acquire_token() -> str:
    """
    Return a valid access token. Silently refreshes if a cached token exists.
    Raises AuthError if no token is cached (user must sign in via /auth/login).
    """
    if not settings.azure_client_id:
        raise AuthError("AZURE_CLIENT_ID is not set.")
    if not settings.azure_client_secret:
        raise AuthError("AZURE_CLIENT_SECRET is not set.")

    cache = _load_cache()
    app = _build_app(cache)

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(settings.graph_scopes, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    raise AuthError("No cached token — user must sign in at /auth/login")


# ── OAuth auth-code flow ──────────────────────────────────────────────────────

def start_auth_flow() -> str:
    """
    Begin the OAuth flow. Returns the Microsoft login URL to redirect the user to.
    Stores the flow state in _pending_flow for use in handle_auth_callback().
    """
    global _pending_flow
    cache = _load_cache()
    app = _build_app(cache)

    flow = app.initiate_auth_code_flow(
        scopes=settings.graph_scopes,
        redirect_uri=settings.azure_redirect_uri,
    )
    _pending_flow = flow
    return flow["auth_uri"]


def handle_auth_callback(callback_params: dict) -> None:
    """
    Complete the OAuth flow after Microsoft redirects back.
    callback_params is the full query-string dict from the /auth/callback request.
    Raises AuthError on failure.
    """
    global _pending_flow
    if not _pending_flow:
        raise AuthError("No pending auth flow — please start from /auth/login")

    cache = _load_cache()
    app = _build_app(cache)

    result = app.acquire_token_by_auth_code_flow(_pending_flow, callback_params)
    _pending_flow = None

    if "access_token" not in result:
        raise AuthError(
            result.get("error_description") or result.get("error") or "Authentication failed"
        )

    _save_cache(cache)
    logger.info("OAuth authentication completed successfully")


def clear_token_cache() -> None:
    path = settings.token_cache_path
    if path.exists():
        path.unlink()
        logger.info("Token cache cleared")


class GraphClient:
    """Thin httpx wrapper that injects a Bearer token on every request."""

    def _get_token(self) -> str:
        return acquire_token()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: dict | None = None) -> Any:
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        response = httpx.get(url, headers=self._headers(), params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, body: dict) -> Any:
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        response = httpx.post(url, headers=self._headers(), json=body, timeout=30)
        response.raise_for_status()
        if response.status_code == 202 or not response.content:
            return {}
        return response.json()

    def patch(self, path: str, body: dict) -> Any:
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        response = httpx.patch(url, headers=self._headers(), json=body, timeout=30)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def delete(self, path: str) -> None:
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        response = httpx.delete(url, headers=self._headers(), timeout=30)
        response.raise_for_status()

    def get_me(self) -> dict:
        return self.get("/me?$select=displayName,mail,userPrincipalName")

    def list_messages(self, folder: str = "Inbox", top: int = 30, skip: int = 0) -> dict:
        # No $orderby — corporate Exchange silently drops it. Callers sort client-side.
        data = self.get(
            f"/me/mailFolders/{folder}/messages",
            params={
                "$select": (
                    "id,subject,from,toRecipients,receivedDateTime,"
                    "sentDateTime,isRead,bodyPreview,conversationId,hasAttachments"
                ),
                "$top": str(top),
                "$skip": str(skip),
                "$count": "true",
            },
        )
        msgs = data.get("value", [])
        sort_key = "sentDateTime" if folder.lower() == "sentitems" else "receivedDateTime"
        msgs.sort(key=lambda m: m.get(sort_key, ""), reverse=True)
        data["value"] = msgs
        return data

    def get_conversation_messages(self, conversation_id: str) -> list[dict]:
        """Return all messages in a conversation, sorted oldest-first."""
        data = self.get(
            "/me/messages",
            params={
                "$filter": f"conversationId eq '{conversation_id}'",
                "$select": (
                    "id,subject,from,toRecipients,ccRecipients,"
                    "receivedDateTime,isRead,body,bodyPreview,conversationId"
                ),
                "$top": "50",
            },
        )
        msgs = data.get("value", [])
        msgs.sort(key=lambda m: m.get("receivedDateTime", ""))
        return msgs

    def get_message(self, message_id: str) -> dict:
        return self.get(
            f"/me/messages/{message_id}",
            params={
                "$select": (
                    "id,subject,from,toRecipients,ccRecipients,bccRecipients,"
                    "receivedDateTime,sentDateTime,isRead,body,conversationId,"
                    "hasAttachments,bodyPreview"
                )
            },
        )

    def mark_read(self, message_id: str, is_read: bool = True) -> None:
        self.patch(f"/me/messages/{message_id}", {"isRead": is_read})

    def move_to_trash(self, message_id: str) -> None:
        self.post(f"/me/messages/{message_id}/move", {"destinationId": "deleteditems"})

    def send_new_email(self, to: list[str], subject: str, body: str, body_type: str = "text") -> None:
        self.post(
            "/me/sendMail",
            {
                "message": {
                    "subject": subject,
                    "body": {"contentType": body_type, "content": body},
                    "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
                },
                "saveToSentItems": True,
            },
        )

    def create_reply_draft(self, message_id: str) -> dict:
        return self.post(f"/me/messages/{message_id}/createReply", {})

    def create_forward_draft(self, message_id: str) -> dict:
        return self.post(f"/me/messages/{message_id}/createForward", {})

    def update_draft(self, draft_id: str, body: dict) -> dict:
        return self.patch(f"/me/messages/{draft_id}", body)

    def send_draft(self, draft_id: str) -> None:
        self.post(f"/me/messages/{draft_id}/send", {})

    def reply_to_message(self, message_id: str, body_text: str, subject: str | None = None) -> dict:
        """Send a reply. Returns the draft dict (contains id, conversationId)."""
        draft = self.create_reply_draft(message_id)
        update: dict = {"body": {"contentType": "text", "content": body_text}}
        if subject:
            update["subject"] = subject
        self.update_draft(draft["id"], update)
        self.send_draft(draft["id"])
        return draft

    def get_latest_sent_message(self) -> dict | None:
        """Return the most recently sent item (id, conversationId, subject, sentDateTime)."""
        data = self.get(
            "/me/mailFolders/SentItems/messages",
            params={"$select": "id,conversationId,subject,sentDateTime", "$top": "1"},
        )
        msgs = data.get("value", [])
        return msgs[0] if msgs else None

    def forward_message(self, message_id: str, to: list[str], body_text: str) -> None:
        draft = self.create_forward_draft(message_id)
        self.update_draft(
            draft["id"],
            {
                "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
                "body": {"contentType": "text", "content": body_text},
            },
        )
        self.send_draft(draft["id"])

    def is_authenticated(self) -> bool:
        cache = _load_cache()
        app = _build_app(cache)
        return bool(app.get_accounts())

    def get_paginated(self, path: str, params: dict | None = None) -> list[dict]:
        results = []
        url: str | None = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        while url:
            data = self.get(url, params=params)
            results.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None
        return results


_client: GraphClient | None = None


def get_graph_client() -> GraphClient:
    global _client
    if _client is None:
        _client = GraphClient()
    return _client
