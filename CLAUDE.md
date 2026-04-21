# Speemail

AI-powered Outlook email assistant. Connects to your Outlook inbox via Microsoft Graph API, uses Claude to draft replies and follow-ups, surfaces them for approval, and also provides a task manager and persistent AI chat panel — all in a local web dashboard.

## Running

```bash
python -m speemail
# Opens http://localhost:8765
```

First run triggers a Microsoft OAuth login. After login, tokens are cached for 90 days.

## Architecture

Two threads run side-by-side:

```
[APScheduler thread]              [FastAPI/uvicorn thread]
  poll_emails_job()  (every 15m)    serves http://localhost:8765
    ├── email_poller.py               ├── auth + password middleware
    │     ├── fetch sent items        ├── inbox browsing (live Graph)
    │     └── fetch inbox unreads     ├── AI queue (approve/edit/reject)
    └── ai_engine.py (Claude)         ├── tasks CRUD
          └── draft reply/follow-up   ├── AI chat panel
                                      └── dashboard + history
                    ↕ both share ↕
               SQLite (data/speemail.db)
```

- **Auth**: MSAL confidential-client OAuth flow (authorization code + PKCE), tokens cached at `data/token_cache.bin`. In production (`SERVER_MODE=true`) a password gate protects all pages.
- **DB**: SQLite + SQLAlchemy 2.x ORM + Alembic migrations
- **Web**: FastAPI + Jinja2 templates + HTMX (no JS build step, no bundler)
- **Scheduler**: APScheduler 3.x `BackgroundScheduler` — stays on 3.x, do NOT upgrade to 4.x (breaking API)
- **AI**: Anthropic Claude (`claude-sonnet-4-6`) for email drafts and the chat panel

## Pages and routes

| Route | Template | Purpose |
|---|---|---|
| `GET /` | `home.html` | Dashboard: needs-reply, awaiting-response, tasks, queue count |
| `GET /inbox` | `inbox.html` | Two-pane inbox browser |
| `GET /queue` | `dashboard.html` | AI draft approval queue |
| `GET /history` | `history.html` | Sent / rejected email log |
| `GET /tasks` | `tasks.html` | Task list with create/update/delete |
| `GET /settings` | `settings.html` | Ignore rules, poll interval |
| `GET /login` | `login.html` | Password gate (production only) |
| `GET /auth/login` | — | Starts OAuth flow |
| `GET /auth/callback` | — | OAuth callback handler |

## Key files

| File | Role |
|---|---|
| `speemail/config.py` | All config via pydantic-settings + `.env` |
| `speemail/auth/graph_auth.py` | MSAL auth + `GraphClient` (httpx wrapper around Graph API) |
| `speemail/middleware/auth_middleware.py` | Password gate middleware (production) |
| `speemail/models/tables.py` | All ORM models (see Database Models below) |
| `speemail/models/database.py` | SQLAlchemy engine + session factory |
| `speemail/scheduler.py` | APScheduler wiring; `poll_emails_job()` entry point |
| `speemail/services/email_poller.py` | Graph queries for sent follow-ups + inbox quick replies |
| `speemail/services/ai_engine.py` | Claude prompts, JSON parsing, confidence scoring |
| `speemail/services/email_sender.py` | Graph `createReply` / send flow |
| `speemail/services/inbox_service.py` | Inbox page browsing helpers (list + detail) |
| `speemail/services/unresponded_service.py` | "Needs Your Reply" detection with 5-min cache |
| `speemail/services/ai_chat.py` | Chat panel AI service — context assembly, tool use, memory |
| `speemail/api/app.py` | FastAPI app factory, lifespan hooks, Jinja2 filters |
| `speemail/api/deps.py` | FastAPI dependency injectors (DB session, Graph client) |
| `speemail/api/routes/dashboard.py` | Home page + debug endpoints |
| `speemail/api/routes/inbox.py` | Inbox list/detail, reply/forward/compose/trash |
| `speemail/api/routes/emails.py` | Approve / edit / reject HTMX endpoints |
| `speemail/api/routes/tasks.py` | Task CRUD endpoints |
| `speemail/api/routes/chat.py` | Chat panel endpoints |
| `speemail/api/routes/settings.py` | Settings + ignore rules endpoints |
| `speemail/api/routes/scheduler_routes.py` | Manual poll trigger, scheduler status |
| `speemail/api/routes/auth.py` | OAuth login / callback |
| `speemail/api/routes/login.py` | Password login page |
| `speemail/static/keyboard.js` | All keyboard shortcut handling (no framework) |
| `speemail/static/style.css` | All styles (single file, no preprocessor) |

## Database models

| Model | Table | Purpose |
|---|---|---|
| `TrackedEmail` | `tracked_emails` | Every email the scheduler has flagged + AI draft |
| `PollCursor` | `poll_cursors` | Watermarks so the scheduler doesn't reprocess old emails |
| `Setting` | `settings` | Key/value config overrides (poll interval, etc.) |
| `Task` | `tasks` | User tasks — title, status, priority, optional due date |
| `UserMemory` | `user_memories` | Facts the AI chat remembers about the user |
| `ChatMessage` | `chat_messages` | Persistent chat history for the AI panel |
| `IgnoreRule` | `ignore_rules` | Sender/subject patterns to skip in "Needs Reply" |

## Email status flow

```
pending_approval → sent       (user approved and email was sent)
pending_approval → rejected   (user rejected, or AI marked as skip)
pending_approval → ai_error   (Claude failed to produce valid JSON after retries)
```

## Scheduler behaviour

- First run is delayed **3 minutes** after startup to avoid OOM during boot
- Each cycle fetches at most **20 sent items** and **10 inbox unreads** (single page, no pagination)
- At most **5 new emails** are passed to Claude per cycle
- Cursor in `poll_cursors` ensures only truly new emails are reprocessed each cycle

## Environment variables (`.env`)

```
AZURE_CLIENT_ID=          # Azure app registration client ID
AZURE_TENANT_ID=common    # or your specific tenant ID
AZURE_CLIENT_SECRET=      # client secret from Azure portal
AZURE_REDIRECT_URI=       # e.g. http://localhost:8765/auth/callback
ANTHROPIC_API_KEY=        # Claude API key
FOLLOW_UP_DAYS=3          # days without reply before flagging as follow-up
POLL_INTERVAL_MINUTES=15
PORT=8765
SERVER_MODE=false         # set true in production to enable password gate
APP_PASSWORD=             # required when SERVER_MODE=true
```

## Database migrations

```bash
# Apply all pending migrations
python -c "from alembic.config import Config; from alembic import command; command.upgrade(Config('alembic.ini'), 'head')"

# Generate a new migration after changing models/tables.py
python -c "from alembic.config import Config; from alembic import command; command.revision(Config('alembic.ini'), autogenerate=True, message='describe change')"
```

## Keyboard shortcuts

| Key | Action |
|---|---|
| `j` / `k` | Navigate up/down in list |
| `Enter` | Open focused message |
| `r` | Reply |
| `f` | Forward |
| `c` | Compose new email |
| `a` / `e` / `x` | Approve / Edit / Reject (AI queue) |
| `#` | Trash message (inbox) |
| `\` | Toggle AI chat panel |
| `g i` | Go to Inbox |
| `g q` | Go to Queue |
| `g h` | Go to History |
| `g s` | Go to Settings |
| `g t` | Go to Tasks |
| `?` or `⌨` button | Show all shortcuts |
| `Esc` | Close modal / back to list |

## Coding conventions

- Route handlers are `def` (not `async def`) — FastAPI runs synchronous handlers in a thread pool, which is correct since all I/O (Graph API, DB) is synchronous
- All Graph API calls go through `GraphClient` in `graph_auth.py` — never call `httpx` directly from services
- Use SQLAlchemy 2.x style: `db.get(Model, id)`, `db.query(Model).filter_by(...)`
- Confidence scores: `>= 0.90` high (green), `0.70–0.89` medium (orange), `< 0.70` low (red)
- Templates use HTMX for dynamic interactions — no React/Vue, no JS build step
- The `data/` directory is gitignored — contains the SQLite DB and MSAL token cache
- Do not add `$orderby` to inbox Graph queries — it silently fails on corporate Exchange mailboxes

## Azure app registration (one-time setup)

1. Go to portal.azure.com → App registrations → New registration
2. Platform: **Web**
3. Redirect URI: `http://localhost:8765/auth/callback` (add `https://your-domain/auth/callback` for production)
4. Certificates & Secrets → New client secret → copy into `AZURE_CLIENT_SECRET`
5. API permissions → Add delegated: `Mail.Read`, `User.Read`, `offline_access`
6. Copy the **Application (client) ID** into `AZURE_CLIENT_ID`
