"""
APScheduler background job that polls email and generates AI drafts.
Runs in a daemon thread alongside the FastAPI server.
"""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from speemail.auth.graph_auth import get_graph_client
from speemail.config import settings
from speemail.models.database import get_session
from speemail.models.tables import Setting
from speemail.services import ai_engine, email_poller

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(daemon=True)
_last_run: datetime | None = None
_last_error: str | None = None


def poll_emails_job() -> None:
    """Core polling job: fetch new emails and generate AI drafts."""
    global _last_run, _last_error

    logger.info("Starting email poll cycle")
    client = get_graph_client()

    # Fetch the authenticated user's display name for AI prompts
    try:
        me = client.get_me()
        user_display_name = me.get("displayName") or me.get("mail", "the user")
    except Exception as exc:
        logger.error("Failed to fetch /me: %s", exc)
        _last_error = str(exc)
        return

    with get_session() as db:
        # 1. Find sent emails needing follow-up
        follow_ups = email_poller.poll_follow_ups(client, db)
        db.flush()  # get IDs without committing

        for email in follow_ups:
            draft = ai_engine.draft_follow_up(email, user_display_name)
            ai_engine.apply_draft_to_email(email, draft)

        # 2. Find inbox emails that may need quick replies
        quick_replies = email_poller.poll_quick_replies(client, db)
        db.flush()

        for email in quick_replies:
            draft = ai_engine.draft_quick_reply(email, user_display_name)
            ai_engine.apply_draft_to_email(email, draft)

    total = len(follow_ups) + len(quick_replies)
    logger.info("Poll cycle complete: %d follow-ups, %d quick replies", len(follow_ups), len(quick_replies))
    _last_run = datetime.utcnow()
    _last_error = None


def _get_interval() -> int:
    """Read poll interval from DB settings (falls back to config)."""
    try:
        with get_session() as db:
            row = db.query(Setting).filter_by(key="poll_interval_minutes").first()
            if row:
                return int(row.value)
    except Exception:
        pass
    return settings.poll_interval_minutes


def start_scheduler() -> None:
    interval = _get_interval()
    logger.info("Starting scheduler (every %d minutes)", interval)
    _scheduler.add_job(
        poll_emails_job,
        trigger="interval",
        minutes=interval,
        id="email_poll",
        replace_existing=True,
        next_run_time=datetime.utcnow(),  # run immediately on startup
    )
    _scheduler.start()


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


def trigger_now() -> None:
    """Manually trigger an immediate poll (called from dashboard)."""
    _scheduler.modify_job("email_poll", next_run_time=datetime.utcnow())


def update_interval(minutes: int) -> None:
    """Update the poll interval without restarting."""
    if _scheduler.running:
        _scheduler.reschedule_job("email_poll", trigger="interval", minutes=minutes)


def get_status() -> dict:
    if not _scheduler.running:
        return {"running": False, "next_run": None, "last_run": None, "last_error": None}

    job = _scheduler.get_job("email_poll")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None

    return {
        "running": True,
        "next_run": next_run,
        "last_run": _last_run.isoformat() if _last_run else None,
        "last_error": _last_error,
    }
