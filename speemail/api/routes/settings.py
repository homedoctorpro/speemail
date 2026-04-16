from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session

from speemail.api.deps import get_db_dep
from speemail.models.tables import Setting
from speemail import scheduler as sched

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

ALLOWED_KEYS = {
    "follow_up_days",
    "poll_interval_minutes",
    "email_signature",
}


def _get_all(db: Session) -> dict[str, str]:
    rows = db.query(Setting).all()
    return {r.key: r.value for r in rows}


def _upsert(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


@router.get("")
def get_settings(db: Session = Depends(get_db_dep)):
    return _get_all(db)


@router.post("")
def update_settings(
    follow_up_days: int = Form(None),
    poll_interval_minutes: int = Form(None),
    email_signature: str = Form(None),
    db: Session = Depends(get_db_dep),
):
    updates: dict[str, str] = {}
    if follow_up_days is not None:
        updates["follow_up_days"] = str(follow_up_days)
    if poll_interval_minutes is not None:
        updates["poll_interval_minutes"] = str(poll_interval_minutes)
        sched.update_interval(poll_interval_minutes)
    if email_signature is not None:
        updates["email_signature"] = email_signature

    for k, v in updates.items():
        _upsert(db, k, v)

    return {"ok": True, "updated": list(updates.keys())}
