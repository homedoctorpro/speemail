"""
Microsoft Graph API authentication via MSAL device-code flow.

First run: prints a URL + code for the user to authenticate in their browser.
Subsequent runs: silently refreshes using the cached refresh token.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import msal

from speemail.config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


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


def _build_app(cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(
        client_id=settings.azure_client_id,
        authority=f"https://login.microsoftonline.com/{settings.azure_tenant_id}",
        token_cache=cache,
    )


def acquire_token() -> str:
    """
    Return a valid access token, refreshing silently if possible.
    Falls back to device-code flow on first run or after cache expiry.
    """
    if not settings.azure_client_id:
        raise AuthError(
            "AZURE_CLIENT_ID is not set. Copy .env.example to .env and fill in your values."
        )

    cache = _load_cache()
    app = _build_app(cache)

    # Try silent acquisition first
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(settings.graph_scopes, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    # Fall back to interactive browser login
    logger.info("No cached token — opening browser for login")
    print("\n" + "=" * 60)
    print("  Speemail is opening your browser to sign in.")
    print("  Sign in with your Microsoft account, then return here.\n")

    result = app.acquire_token_interactive(
        scopes=settings.graph_scopes,
        prompt="select_account",
    )
    if "access_token" not in result:
        raise AuthError(
            f"Authentication failed: {result.get('error_description', result.get('error'))}"
        )

    _save_cache(cache)
    logger.info("Authentication successful")
    return result["access_token"]


def clear_token_cache() -> None:
    """Remove the cached token (logout)."""
    path = settings.token_cache_path
    if path.exists():
        path.unlink()
        logger.info("Token cache cleared")


class GraphClient:
    """
    Thin wrapper around httpx that injects a valid Bearer token on every request.
    Automatically refreshes the token before it expires.
    """

    def __init__(self) -> None:
        self._token: str | None = None

    def _get_token(self) -> str:
        # Always try silent refresh — MSAL handles expiry internally
        self._token = acquire_token()
        return self._token

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
        # Some Graph POST endpoints return 202 Accepted with no body
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

    # ── Inbox / folder browsing ────────────────────────────────────────────

    def list_messages(self, folder: str = "Inbox", top: int = 30, skip: int = 0) -> dict:
        """Return one page of messages with a count and nextLink."""
        return self.get(
            f"/me/mailFolders/{folder}/messages",
            params={
                "$select": (
                    "id,subject,from,toRecipients,receivedDateTime,"
                    "sentDateTime,isRead,bodyPreview,conversationId,hasAttachments"
                ),
                "$top": str(top),
                "$skip": str(skip),
                "$orderby": "receivedDateTime desc",
                "$count": "true",
            },
        )

    def get_message(self, message_id: str) -> dict:
        """Fetch full message with body HTML."""
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

    # ── Sending ────────────────────────────────────────────────────────────

    def send_new_email(self, to: list[str], subject: str, body: str, body_type: str = "text") -> None:
        """Send a brand-new email."""
        self.post(
            "/me/sendMail",
            {
                "message": {
                    "subject": subject,
                    "body": {"contentType": body_type, "content": body},
                    "toRecipients": [
                        {"emailAddress": {"address": addr}} for addr in to
                    ],
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

    def reply_to_message(
        self, message_id: str, body_text: str, subject: str | None = None
    ) -> None:
        """Create a reply draft, update body, send."""
        draft = self.create_reply_draft(message_id)
        draft_id = draft["id"]
        update: dict = {"body": {"contentType": "text", "content": body_text}}
        if subject:
            update["subject"] = subject
        self.update_draft(draft_id, update)
        self.send_draft(draft_id)

    def forward_message(self, message_id: str, to: list[str], body_text: str) -> None:
        """Create a forward draft, set recipients and body, send."""
        draft = self.create_forward_draft(message_id)
        draft_id = draft["id"]
        self.update_draft(
            draft_id,
            {
                "toRecipients": [
                    {"emailAddress": {"address": addr}} for addr in to
                ],
                "body": {"contentType": "text", "content": body_text},
            },
        )
        self.send_draft(draft_id)

    def is_authenticated(self) -> bool:
        cache = _load_cache()
        app = _build_app(cache)
        return bool(app.get_accounts())

    def get_paginated(self, path: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages of a Graph API list response."""
        results = []
        url: str | None = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        while url:
            data = self.get(url, params=params)
            results.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None  # nextLink already contains params
        return results


# Module-level singleton used by services
_client: GraphClient | None = None


def get_graph_client() -> GraphClient:
    global _client
    if _client is None:
        _client = GraphClient()
    return _client
