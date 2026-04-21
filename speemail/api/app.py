from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from speemail import scheduler
from speemail.api.routes import (
    auth,
    chat,
    dashboard,
    emails,
    inbox,
    login,
    scheduler_routes,
    settings,
    tasks,
)
from speemail.middleware.auth_middleware import PasswordAuthMiddleware
from speemail.models.database import init_db


def _run_migrations() -> None:
    try:
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config("alembic.ini"), "head")
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Migration skipped: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _run_migrations()
    init_db()
    scheduler.start_scheduler()
    yield
    scheduler.stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Speemail",
        description="AI-powered Outlook email follow-up assistant",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(PasswordAuthMiddleware)

    static_dir = Path(__file__).parent.parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    templates_dir = Path(__file__).parent.parent / "templates"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))
    _register_template_filters(app.state.templates)

    app.include_router(auth.router)
    app.include_router(login.router)
    app.include_router(dashboard.router)
    app.include_router(inbox.router)
    app.include_router(emails.router)
    app.include_router(settings.router)
    app.include_router(scheduler_routes.router)
    app.include_router(tasks.router)
    app.include_router(chat.router)

    return app


def _register_template_filters(templates: Jinja2Templates) -> None:
    from datetime import datetime

    def timeago(dt: datetime | str | None) -> str:
        if dt is None:
            return "never"
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00")).replace(tzinfo=None)
        now = datetime.utcnow()
        diff = now - dt
        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        if days > 1:
            return f"{days} days ago"
        if days == 1:
            return "yesterday"
        if hours > 1:
            return f"{hours} hours ago"
        if hours == 1:
            return "1 hour ago"
        if minutes > 1:
            return f"{minutes} minutes ago"
        return "just now"

    def confidence_color(score: float | None) -> str:
        if score is None:
            return "gray"
        if score >= 0.90:
            return "green"
        if score >= 0.70:
            return "orange"
        return "red"

    def confidence_pct(score: float | None) -> int:
        if score is None:
            return 0
        return int(score * 100)

    templates.env.filters["timeago"] = timeago
    templates.env.filters["confidence_color"] = confidence_color
    templates.env.filters["confidence_pct"] = confidence_pct


app = create_app()
