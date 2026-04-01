# Speemail

AI-powered Outlook email follow-up assistant. Watches your Outlook inbox/sent folder via Microsoft Graph API, uses Claude to draft replies and follow-ups, and surfaces them for approval in a local web dashboard.

## Running

```bash
python -m speemail
# Opens http://localhost:8765
```

First run triggers a Microsoft device-code login (one-time; tokens cached for 90 days).

## Architecture

```
[APScheduler thread]              [FastAPI thread]
  poll_emails_job()                 serves http://localhost:8765
    ├── email_poller.py  ──────────────── SQLite (data/speemail.db)
    └── ai_engine.py (Claude)       └── approve → email_sender.py → Graph API
```

- **Auth**: MSAL device-code flow, tokens cached at `data/token_cache.bin`
- **DB**: SQLite + SQLAlchemy 2.x + Alembic migrations
- **Web**: FastAPI + Jinja2 templates + HTMX (no JS build step)
- **Scheduler**: APScheduler 3.x `BackgroundScheduler` (stay on 3.x, not 4.x)

## Keyboard shortcuts

| Key | Action |
|---|---|
| `j` / `k` | Navigate up/down in list |
| `Enter` | Open focused message |
| `r` | Reply |
| `f` | Forward |
| `c` | Compose new email |
| `a` / `e` / `x` | Approve / Edit / Reject (queue) |
| `#` | Trash message (inbox) |
| `g i/q/h/s` | Go to Inbox / Queue / History / Settings |
| `?` or `⌨` button | Show all shortcuts |
| `Esc` | Close modal / back to list |

## Key files

| File | Role |
|---|---|
| `speemail/config.py` | All config via pydantic-settings + `.env` |
| `speemail/auth/graph_auth.py` | MSAL auth + `GraphClient` (httpx wrapper) |
| `speemail/models/tables.py` | ORM models: `TrackedEmail`, `PollCursor`, `Setting` |
| `speemail/services/email_poller.py` | Graph queries for sent follow-ups + inbox quick replies |
| `speemail/services/inbox_service.py` | Inbox page browsing helpers (list + detail) |
| `speemail/api/routes/inbox.py` | Inbox page, message detail, reply/forward/compose/trash endpoints |
| `speemail/static/keyboard.js` | All keyboard shortcut handling (no framework) |
| `speemail/services/ai_engine.py` | Claude prompts, JSON parsing, confidence scoring |
| `speemail/services/email_sender.py` | Graph `createReply` → send flow |
| `speemail/scheduler.py` | Background poll job wiring |
| `speemail/api/app.py` | FastAPI app factory, lifespan, template filters |
| `speemail/api/routes/emails.py` | Approve / edit / reject HTMX endpoints |

## Environment variables (`.env`)

```
AZURE_CLIENT_ID=        # Azure app registration client ID
AZURE_TENANT_ID=common  # or your tenant ID
ANTHROPIC_API_KEY=      # Claude API key
FOLLOW_UP_DAYS=3        # days without reply before flagging
POLL_INTERVAL_MINUTES=15
AUTO_SEND_THRESHOLD=0.95
PORT=8765
```

## Database migrations

```bash
# Apply migrations (already run on first setup)
python -c "from alembic.config import Config; from alembic import command; command.upgrade(Config('alembic.ini'), 'head')"

# Create a new migration after changing models/tables.py
python -c "from alembic.config import Config; from alembic import command; command.revision(Config('alembic.ini'), autogenerate=True, message='describe change')"
```

## Email status flow

```
pending_approval → sent       (user approved)
pending_approval → rejected   (user rejected, or AI skipped quick_reply)
pending_approval → ai_error   (Claude failed to produce valid JSON after 2 retries)
sent → auto_sent              (future: high-confidence auto-send)
```

## Coding conventions

- FastAPI route handlers are `def` (not `async def`) — FastAPI runs them in a thread pool, which is correct since all I/O (Graph API, DB) is synchronous
- Use SQLAlchemy 2.x style: `db.get(Model, id)`, `db.query(Model).filter_by(...)` — no legacy `session.query` patterns from 1.x
- All Graph API calls go through `GraphClient` in `graph_auth.py` — never call `httpx` directly from services
- Confidence scores: `>= 0.90` high (green), `0.70–0.89` medium (orange), `< 0.70` low (red)
- Templates use HTMX for dynamic interactions — no React/Vue, no JS build step
- The `data/` directory is gitignored — contains the SQLite DB and MSAL token cache

## Azure app registration (one-time setup)

1. Go to portal.azure.com → App registrations → New registration
2. Platform: **Mobile and desktop**
3. Redirect URI: `https://login.microsoftonline.com/common/oauth2/nativeclient`
4. Authentication → Advanced settings → **Allow public client flows: Yes**
5. API permissions → Add: `Mail.Read`, `Mail.ReadWrite`, `Mail.Send`, `offline_access`, `User.Read` (all Delegated)
6. Copy the **Application (client) ID** into `.env`
