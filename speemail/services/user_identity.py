"""Persists the authenticated user's email and display name to the settings table."""
from __future__ import annotations

from sqlalchemy.orm import Session

from speemail.models.tables import Setting


def save_user_identity(db: Session, me: dict) -> None:
    email = me.get("mail") or me.get("userPrincipalName", "")
    name = me.get("displayName", "")
    for key, val in [("user_email", email), ("user_name", name)]:
        row = db.query(Setting).filter_by(key=key).first()
        if row:
            row.value = val
        else:
            db.add(Setting(key=key, value=val))
