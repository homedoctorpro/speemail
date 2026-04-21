from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from speemail import scheduler as sched
from speemail.api.deps import get_db_dep
from speemail.models.tables import EmailFeedback, IgnoreRule, Setting
from speemail.services import classification_service, unresponded_service

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

ALLOWED_KEYS = {
    "follow_up_days",
    "poll_interval_minutes",
    "email_signature",
    "unresponded_scan_days",
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
    unresponded_scan_days: int = Form(None),
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
    if unresponded_scan_days is not None:
        updates["unresponded_scan_days"] = str(max(1, unresponded_scan_days))
        unresponded_service.invalidate_cache()

    for k, v in updates.items():
        _upsert(db, k, v)

    return {"ok": True, "updated": list(updates.keys())}


# ── Classification rules ─────────────────────────────────────────────────────

@router.get("/classification-rules", response_class=HTMLResponse)
def get_classification_rules(request: Request, db: Session = Depends(get_db_dep)):
    rules_row = db.query(Setting).filter_by(key=classification_service.RULES_SETTING_KEY).first()
    feedback_count = db.query(EmailFeedback).count()
    return request.app.state.templates.TemplateResponse(
        "partials/_classification_rules.html",
        {
            "request": request,
            "rules": rules_row.value if rules_row else None,
            "feedback_count": feedback_count,
            "derive_threshold": classification_service.DERIVE_AFTER_N_FEEDBACKS,
        },
    )


@router.post("/classification-rules/derive", response_class=HTMLResponse)
def force_derive_rules(request: Request, db: Session = Depends(get_db_dep)):
    rules = classification_service.derive_rules(db)
    feedback_count = db.query(EmailFeedback).count()
    return request.app.state.templates.TemplateResponse(
        "partials/_classification_rules.html",
        {
            "request": request,
            "rules": rules,
            "feedback_count": feedback_count,
            "derive_threshold": classification_service.DERIVE_AFTER_N_FEEDBACKS,
        },
    )


# ── Ignore rules ──────────────────────────────────────────────────────────────

@router.get("/ignore-rules", response_class=HTMLResponse)
def list_ignore_rules(request: Request, db: Session = Depends(get_db_dep)):
    rules = db.query(IgnoreRule).order_by(IgnoreRule.created_at).all()
    return request.app.state.templates.TemplateResponse(
        "partials/_ignore_rules.html",
        {"request": request, "rules": rules},
    )


@router.post("/ignore-rules", response_class=HTMLResponse)
def add_ignore_rule(
    request: Request,
    db: Session = Depends(get_db_dep),
    rule_type: str = Form(...),
    pattern: str = Form(...),
):
    pattern = pattern.strip()
    if pattern:
        rule = IgnoreRule(rule_type=rule_type, pattern=pattern)
        db.add(rule)
        db.flush()
        unresponded_service.invalidate_cache()

    rules = db.query(IgnoreRule).order_by(IgnoreRule.created_at).all()
    return request.app.state.templates.TemplateResponse(
        "partials/_ignore_rules.html",
        {"request": request, "rules": rules},
    )


@router.delete("/ignore-rules/{rule_id}", response_class=HTMLResponse)
def delete_ignore_rule(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db_dep),
):
    rule = db.get(IgnoreRule, rule_id)
    if rule:
        db.delete(rule)
        db.flush()
        unresponded_service.invalidate_cache()

    rules = db.query(IgnoreRule).order_by(IgnoreRule.created_at).all()
    return request.app.state.templates.TemplateResponse(
        "partials/_ignore_rules.html",
        {"request": request, "rules": rules},
    )
