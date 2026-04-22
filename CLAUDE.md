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
    ├── ai_engine.py (Claude)         ├── tasks CRUD
    │     └── draft reply/follow-up   ├── AI chat panel
    ├── sent_classification_service   └── dashboard + history
    │     └── classify sent emails
    │         expects reply? → WatchedThread
    └── watched_threads_service
          └── check_replies() each poll
                    ↕ both share ↕
               SQLite (data/speemail.db)
```

- **Auth**: MSAL confidential-client OAuth flow (authorization code + PKCE), tokens cached at `data/token_cache.bin`. In production (`SERVER_MODE=true`) a password gate protects all pages.
- **DB**: SQLite + SQLAlchemy 2.x ORM + Alembic migrations
- **Web**: FastAPI + Jinja2 templates + HTMX (no JS build step, no bundler)
- **Scheduler**: APScheduler 3.x `BackgroundScheduler` — stays on 3.x, do NOT upgrade to 4.x (breaking API)
- **AI**: Anthropic Claude (`claude-sonnet-4-6`) for email drafts, classification, and the chat panel

## Pages and routes

| Route | Template | Purpose |
|---|---|---|
| `GET /` | `home.html` | Dashboard: needs-reply, watched threads, tasks, queue count |
| `GET /inbox` | `inbox.html` | Two-pane inbox — All / Needs Reply / Awaiting tabs |
| `GET /queue` | `dashboard.html` | AI draft approval queue |
| `GET /history` | `history.html` | Sent / rejected email log |
| `GET /tasks` | `tasks.html` | Task list with create/update/delete |
| `GET /settings` | `settings.html` | AI rules, ignore filters, thresholds, poll interval |
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
| `speemail/services/inbox_service.py` | Inbox browsing: list (threaded), detail (full conversation) |
| `speemail/services/unresponded_service.py` | "Needs Your Reply" with stale-while-revalidate cache |
| `speemail/services/classification_service.py` | Incoming email classification — confidence scoring, rule derivation, salutation check |
| `speemail/services/sent_classification_service.py` | Outgoing email classification — expects reply? → auto-watch |
| `speemail/services/watched_threads_service.py` | WatchedThread CRUD, reply detection, overdue alerts |
| `speemail/services/ai_chat.py` | Chat panel AI service — context assembly, tool use, memory |
| `speemail/api/app.py` | FastAPI app factory, lifespan hooks, Jinja2 filters |
| `speemail/api/deps.py` | FastAPI dependency injectors (DB session, Graph client) |
| `speemail/api/routes/dashboard.py` | Home page + debug endpoints |
| `speemail/api/routes/inbox.py` | Inbox list/detail, reply/forward/compose/trash |
| `speemail/api/routes/emails.py` | Approve / edit / reject HTMX endpoints |
| `speemail/api/routes/tasks.py` | Task CRUD endpoints |
| `speemail/api/routes/chat.py` | Chat panel endpoints |
| `speemail/api/routes/settings.py` | Settings + ignore rules + classification rules endpoints |
| `speemail/api/routes/watched_threads.py` | Watch/unwatch, reply feedback, inbox watch endpoints |
| `speemail/api/routes/scheduler_routes.py` | Manual poll trigger, scheduler status |
| `speemail/api/routes/auth.py` | OAuth login / callback |
| `speemail/api/routes/login.py` | Password login page |
| `speemail/static/keyboard.js` | All keyboard shortcut handling (no framework) |
| `speemail/static/style.css` | All styles — single file, includes dark mode via `[data-theme="dark"]` |
| `speemail/static/logo.svg` | App logo — anthropomorphized running envelope character |

## Database models

| Model | Table | Purpose |
|---|---|---|
| `TrackedEmail` | `tracked_emails` | Every email the scheduler has flagged + AI draft |
| `PollCursor` | `poll_cursors` | Watermarks so the scheduler doesn't reprocess old emails |
| `Setting` | `settings` | Key/value config overrides (poll interval, thresholds, etc.) |
| `Task` | `tasks` | User tasks — title, status, priority, optional due date |
| `UserMemory` | `user_memories` | Facts the AI chat remembers about the user |
| `ChatMessage` | `chat_messages` | Persistent chat history for the AI panel |
| `IgnoreRule` | `ignore_rules` | Sender/subject patterns to skip in "Needs Reply" |
| `EmailClassification` | `email_classifications` | AI classification results for incoming emails (needs_reply, confidence) |
| `EmailFeedback` | `email_feedback` | User ✓/✕/resolved feedback on "Needs Your Reply" decisions |
| `WatchedThread` | `watched_threads` | Threads being monitored for replies — source: `auto`, `manual_sent`, `manual_inbox` |
| `SentEmailScan` | `sent_email_scans` | Cache of AI classifications for outgoing emails (expects reply?) |

## Email status flow

```
pending_approval → sent       (user approved and email was sent)
pending_approval → rejected   (user rejected, or AI marked as skip)
pending_approval → ai_error   (Claude failed to produce valid JSON after retries)
```

## Scheduler behaviour

Each poll cycle (default 15 min):
1. Fetch up to 10 new inbox unreads → run through `email_poller` for quick-reply detection
2. Pass up to 5 new emails to Claude (`ai_engine`) to draft replies
3. Fetch up to 50 recent sent items → `sent_classification_service.scan_sent_items()` — classifies each for "expects reply", creates `WatchedThread` for those that do
4. `watched_threads_service.check_replies()` — polls Graph for replies on active watched threads, marks them resolved

Other timing notes:
- First run delayed **3 minutes** after startup to avoid OOM during boot
- Cursor in `poll_cursors` ensures only truly new emails are reprocessed

## AI classification & learning

### Incoming emails ("Needs Your Reply")
- `classification_service.classify()` scores each email `needs_reply: bool` + `confidence: 0.0–1.0`
- **Salutation mismatch fast-path**: if the email addresses someone other than the user by first name, confidence is clamped to 0.10 (almost certainly not for them)
- Emails only surface in "Needs Your Reply" if `needs_reply=True AND confidence >= threshold` (default 50%, configurable in Settings)
- User feedback: **✓ Yes** (`needs_reply`), **✗ No** (`skip`), **✓ Done** (`resolved` — handled another way; not counted as skip)
- Every 10 non-resolved feedbacks triggers `derive_rules()` — Claude synthesises raw examples into persistent rules stored in `Setting`, replacing the raw few-shot examples

### Outgoing emails (auto-watch)
- `sent_classification_service.classify_sent()` scores each sent email: `expects_reply: bool` + `confidence`
- High-confidence "expects reply" → `WatchedThread` created automatically (source=`auto`)
- User can confirm or deny auto-watched threads from the home dashboard
- Same two-stage learning: feedback → rule derivation after N examples

### Thread activity
- When building the "Needs Your Reply" list, the service annotates emails that have seen thread activity (someone else replied) with a badge showing the latest responder count — signals you may not need to reply

## Inbox features

### Tabs
Three tabs load **in parallel** on page open (HTMX `hx-trigger="load"` on all three), tab switching is instant CSS show/hide:
- **All** — full inbox, grouped by conversation
- **Needs Reply** — AI-flagged emails above confidence threshold
- **Awaiting** — threads you're waiting on a reply for

### Email threading
- List view: emails grouped by `conversationId`; threads show a count badge
- Detail view: full conversation thread loaded via `get_conversation_messages()`; older messages collapsed as `<details>`, latest fully expanded
- Note: no `$orderby` used — client-side sort by `receivedDateTime` instead (Exchange silently drops `$orderby`)

### Color coding (inbox list)
| State | Color | Meaning |
|---|---|---|
| `state-needs_reply` | Amber left border + tint | AI flagged, needs your response |
| `state-watched` | Blue left border + tint | Manually watched incoming thread |
| `state-awaiting` | Teal left border + tint | You sent something, waiting on reply |

### Watched threads (👁 Watch button)
- In the detail pane: "👁 Watch" button on any incoming email watches the conversation for replies (source=`manual_inbox`)
- In reply/compose modals: "Watch this thread" checkbox auto-watches after sending (source=`manual_sent`)
- Overdue alert threshold configurable in Settings (default 48 hours)
- Home dashboard shows all active watched threads; overdue ones highlighted in amber

## "Needs Your Reply" cache

Uses stale-while-revalidate: returns instantly from cache on every home page load, triggers a background thread refresh when stale. Only shows a loading spinner on the very first-ever load (when `data is None`). Cache TTL is 5 minutes.

## Dark mode

- Default theme is **dark**. Preference stored in `localStorage` under key `theme`.
- Theme applied immediately in `<head>` (before CSS renders) via an inline script setting `data-theme` on `<html>` — no flash of wrong theme.
- Toggle via the 🌙/☀️ button in the nav bar.
- All dark overrides live in the `[data-theme="dark"]` block at the bottom of `style.css` — overrides CSS variables plus hardcoded color selectors.

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

## Settings keys (stored in `settings` table)

| Key | Default | Purpose |
|---|---|---|
| `follow_up_days` | `3` | Days before flagging sent email as needing follow-up |
| `poll_interval_minutes` | `15` | Scheduler poll frequency |
| `unresponded_scan_days` | `90` | How far back to scan for unresponded emails |
| `watched_thread_alert_hours` | `48` | Hours before a watched thread is marked overdue |
| `needs_reply_min_confidence` | `50` | Min AI confidence (0–100) to show in "Needs Your Reply" |
| `email_signature` | `""` | Appended to all drafted emails |
| `user_name` | `""` | User's display name — used for salutation mismatch detection |
| `classification_rules` | `""` | AI-derived rules for incoming classification (auto-updated) |
| `sent_classification_rules` | `""` | AI-derived rules for outgoing classification (auto-updated) |

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
| `j` / `↑` | Previous message (up) |
| `k` / `↓` | Next message (down) |
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

Note: `j`=up / `k`=down is intentionally reversed from Gmail convention — this is the user's preference.

## Coding conventions

- Route handlers are `def` (not `async def`) — FastAPI runs synchronous handlers in a thread pool, which is correct since all I/O (Graph API, DB) is synchronous
- All Graph API calls go through `GraphClient` in `graph_auth.py` — never call `httpx` directly from services
- Use SQLAlchemy 2.x style: `db.get(Model, id)`, `db.query(Model).filter_by(...)`
- Confidence scores: `>= 0.90` high (green), `0.70–0.89` medium (orange), `< 0.70` low (red)
- Templates use HTMX for dynamic interactions — no React/Vue, no JS build step
- The `data/` directory is gitignored — contains the SQLite DB and MSAL token cache
- Do not add `$orderby` to inbox Graph queries — it silently fails on corporate Exchange mailboxes
- `urlencode_value` Jinja2 filter exists for URL-encoding Graph message IDs (they contain `+`/`=`)

## Azure app registration (one-time setup)

1. Go to portal.azure.com → App registrations → New registration
2. Platform: **Web**
3. Redirect URI: `http://localhost:8765/auth/callback` (add `https://your-domain/auth/callback` for production)
4. Certificates & Secrets → New client secret → copy into `AZURE_CLIENT_SECRET`
5. API permissions → Add delegated: `Mail.Read`, `User.Read`, `offline_access`
6. Copy the **Application (client) ID** into `AZURE_CLIENT_ID`
