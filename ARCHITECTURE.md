# Speemail Architecture

## System Overview

```mermaid
graph TB
    subgraph Browser["Browser"]
        UI["HTMX + Jinja2 UI"]
    end

    subgraph Speemail["Speemail Process (uvicorn)"]
        subgraph FastAPI["FastAPI thread"]
            MW["PasswordAuthMiddleware"]
            Routes["Route handlers"]
            Templates["Jinja2 templates"]
        end

        subgraph Scheduler["APScheduler thread"]
            Job["poll_emails_job()"]
            Poller["email_poller.py"]
            AIEngine["ai_engine.py (Claude)"]
        end

        subgraph Services["Services"]
            InboxSvc["inbox_service.py"]
            UnrespSvc["unresponded_service.py"]
            ChatSvc["ai_chat.py"]
            Sender["email_sender.py"]
        end

        DB[("SQLite\ndata/speemail.db")]
        TokenCache[("MSAL token cache\ndata/token_cache.bin")]
    end

    subgraph External["External APIs"]
        Graph["Microsoft Graph API\ngraph.microsoft.com"]
        Claude["Anthropic API\nclaude-sonnet-4-6"]
        MSAL["Microsoft Auth\nlogin.microsoftonline.com"]
    end

    Browser -- "HTTP (HTMX)" --> FastAPI
    MW --> Routes
    Routes --> Templates
    Routes --> Services
    Routes --> DB

    Job --> Poller
    Poller --> AIEngine
    Poller --> DB
    AIEngine --> Claude

    Services --> Graph
    Services --> DB
    Sender --> Graph

    Routes --> MSAL
    MSAL --> TokenCache
    TokenCache --> Graph
```

## Request Flow — Inbox

```mermaid
sequenceDiagram
    participant B as Browser
    participant R as inbox.py route
    participant S as inbox_service.py
    participant G as Graph API

    B->>R: GET /inbox
    R-->>B: inbox.html (shell only)

    B->>R: GET /api/v1/inbox/messages
    R->>S: get_messages_page()
    S->>G: GET /me/mailFolders/Inbox/messages
    G-->>S: {value: [...], @odata.nextLink}
    S-->>R: {messages, has_more, next_link}
    R-->>B: _message_list.html partial

    B->>R: GET /api/v1/inbox/messages/{id}
    R->>S: get_message_detail()
    S->>G: GET /me/messages/{id}
    G-->>S: full message with body
    R-->>B: _message_detail.html partial
```

## Request Flow — AI Draft Approval

```mermaid
sequenceDiagram
    participant Sched as Scheduler (background)
    participant Poller as email_poller.py
    participant AI as ai_engine.py
    participant DB as SQLite
    participant B as Browser
    participant R as emails.py route
    participant Sender as email_sender.py
    participant G as Graph API

    Sched->>Poller: poll_emails_job() every 15 min
    Poller->>G: fetch sent items / inbox
    G-->>Poller: messages
    Poller->>DB: save TrackedEmail (status=pending_approval)
    Poller->>AI: draft_follow_up() / draft_quick_reply()
    AI->>G: Claude API
    G-->>AI: draft JSON
    AI->>DB: update TrackedEmail (ai_draft_body, confidence)

    B->>R: GET /queue → dashboard.html
    B->>R: GET /api/v1/emails (HTMX)
    R->>DB: query pending_approval
    R-->>B: _email_card_list.html

    B->>R: POST /api/v1/emails/{id}/approve
    R->>Sender: send_reply()
    Sender->>G: createReply → send
    R->>DB: status=sent
    R-->>B: toast + remove card
```

## AI Chat Panel Flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant R as chat.py route
    participant S as ai_chat.py
    participant DB as SQLite
    participant C as Claude API

    B->>R: POST /api/v1/chat {message}
    R->>S: handle_message()
    S->>DB: load UserMemory rows
    S->>DB: load last 20 ChatMessages
    S->>DB: load open Tasks
    S->>C: messages + system prompt + tools
    C-->>S: tool_use (create_task / add_memory / search_emails)
    S->>DB: execute tool (insert Task / UserMemory)
    S->>C: tool_result
    C-->>S: final text response
    S->>DB: save ChatMessage (user + assistant)
    R-->>B: _chat_new_messages.html partial
```

## Database Schema

```mermaid
erDiagram
    TrackedEmail {
        int id PK
        string graph_message_id UK
        string graph_conversation_id
        string email_type
        string status
        string original_subject
        string original_from
        string original_to
        text original_body_preview
        text original_body_html
        datetime sent_at
        string ai_draft_subject
        text ai_draft_body
        float ai_confidence_score
        text ai_reasoning
        text user_edited_body
        datetime final_sent_at
        datetime created_at
        datetime updated_at
    }

    PollCursor {
        int id PK
        string cursor_name UK
        datetime last_checked
    }

    Setting {
        string key PK
        text value
    }

    Task {
        int id PK
        string title
        text description
        string status
        string priority
        datetime due_date
        int related_email_id
        datetime created_at
        datetime updated_at
    }

    UserMemory {
        int id PK
        string memory_type
        text content
        string source
        datetime created_at
        datetime updated_at
    }

    ChatMessage {
        int id PK
        string role
        text content
        datetime created_at
    }

    IgnoreRule {
        int id PK
        string rule_type
        string pattern
        datetime created_at
    }
```

## File Map

```
speemail/
├── __main__.py               Entry point — starts uvicorn
├── main.py                   IPv4 socket patch (Fly.io compat), imports app
├── config.py                 All settings via pydantic-settings + .env
├── scheduler.py              APScheduler wiring — poll_emails_job()
│
├── auth/
│   └── graph_auth.py         MSAL OAuth flow + GraphClient (httpx wrapper)
│
├── middleware/
│   └── auth_middleware.py    Password gate (production SERVER_MODE)
│
├── models/
│   ├── database.py           SQLAlchemy engine + session factory
│   └── tables.py             ORM models: TrackedEmail, Task, UserMemory, etc.
│
├── services/
│   ├── email_poller.py       Fetch sent/inbox emails; deduplicate via DB cursor
│   ├── ai_engine.py          Claude prompts for follow-up / quick-reply drafts
│   ├── email_sender.py       Graph createReply + send
│   ├── inbox_service.py      Inbox browsing helpers (list page, message detail)
│   ├── unresponded_service.py  "Needs Your Reply" detection with 5-min cache
│   └── ai_chat.py            Chat panel — context assembly, tool use, memory
│
├── api/
│   ├── app.py                FastAPI app factory, lifespan, Jinja2 filters
│   ├── deps.py               Dependency injectors (DB session, GraphClient)
│   └── routes/
│       ├── auth.py           OAuth /auth/login + /auth/callback
│       ├── login.py          Password login page
│       ├── dashboard.py      Home page, needs-reply HTMX, debug endpoints
│       ├── inbox.py          Inbox list/detail, reply/forward/compose/trash
│       ├── emails.py         AI queue approve/edit/reject
│       ├── tasks.py          Task CRUD
│       ├── chat.py           Chat panel send/history/clear
│       ├── settings.py       Settings + ignore rules
│       └── scheduler_routes.py  Manual poll trigger, status
│
├── templates/
│   ├── base.html             Layout shell — nav, chat panel, keyboard shortcut modal
│   ├── home.html             Dashboard (needs-reply, awaiting, tasks, queue count)
│   ├── inbox.html            Two-pane inbox
│   ├── dashboard.html        AI queue
│   ├── history.html          Sent/rejected log
│   ├── tasks.html            Task list
│   ├── settings.html         Settings page
│   ├── login.html            Password login
│   ├── device_flow.html      OAuth device flow page
│   ├── auth_error.html       Auth error page
│   └── partials/
│       ├── _message_list.html      Inbox message rows
│       ├── _message_detail.html    Open message + reply/forward buttons
│       ├── _email_card.html        Single AI queue card
│       ├── _email_card_list.html   AI queue list
│       ├── _unresponded_list.html  Needs-reply / awaiting-response rows
│       ├── _task_card.html         Single task card
│       ├── _task_list.html         Task list
│       ├── _chat_messages.html     Full chat history
│       ├── _chat_new_messages.html New messages appended after send
│       ├── _compose_modal.html     New email modal
│       ├── _reply_modal.html       Reply modal
│       ├── _forward_modal.html     Forward modal
│       ├── _edit_modal.html        Edit AI draft modal
│       ├── _history_list.html      History rows
│       ├── _ignore_rules.html      Ignore rule list + add form
│       └── _toast.html             Toast notification
│
└── static/
    ├── keyboard.js           All keyboard shortcuts (no framework)
    └── style.css             All styles (single file)
```
