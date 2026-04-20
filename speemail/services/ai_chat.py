"""
AI chat service with persistent memory and tool use.
Gives Claude access to user memories, tasks, and email context.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date
from typing import Any

import anthropic

from speemail.auth.graph_auth import GraphClient
from speemail.config import settings
from speemail.models.tables import ChatMessage, Task, TrackedEmail, UserMemory

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

_TOOLS = [
    {
        "name": "create_task",
        "description": "Create a new task for the user. Use when the user asks to add, create, or remember a task or to-do item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title"},
                "description": {"type": "string", "description": "Optional longer description"},
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Task priority",
                },
                "due_date": {
                    "type": "string",
                    "description": "Optional due date in YYYY-MM-DD format",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "add_memory",
        "description": "Save a fact about the user to persistent memory. Use when the user shares personal info, preferences, project context, or key contacts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact to remember"},
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "preference", "project", "contact"],
                    "description": "Category of memory",
                },
            },
            "required": ["content", "memory_type"],
        },
    },
    {
        "name": "list_tasks",
        "description": "Retrieve the user's current tasks from the database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "enum": ["all", "todo", "in_progress", "done"],
                    "description": "Filter tasks by status. Default is 'all' open tasks.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_emails",
        "description": "Search the user's emails using a keyword or phrase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term or phrase"},
            },
            "required": ["query"],
        },
    },
]


def _build_system_prompt(db: Any, user_name: str) -> str:
    memories = db.query(UserMemory).order_by(UserMemory.created_at).all()
    open_tasks = db.query(Task).filter(Task.status != "done").order_by(Task.created_at.desc()).all()
    queue_count = db.query(TrackedEmail).filter_by(status="pending_approval").count()

    memory_lines = "\n".join(f"- [{m.memory_type}] {m.content}" for m in memories) or "None yet."
    task_lines = "\n".join(
        f"- [{t.priority}] {t.title} ({t.status})" + (f" — due {t.due_date.date()}" if t.due_date else "")
        for t in open_tasks
    ) or "No open tasks."

    today = datetime.utcnow().strftime("%A, %B %d, %Y")

    return f"""You are Speemail, a personal email and productivity assistant for {user_name}.
Today is {today}.

## What you know about {user_name}:
{memory_lines}

## Current open tasks:
{task_lines}

## Email queue:
- {queue_count} AI-drafted email(s) pending approval in the queue

You can create tasks, save memories, list tasks, and search emails using your tools.
Be concise and action-oriented. When you take an action, confirm it briefly.
If the user shares personal info or context, save it as a memory automatically."""


def _execute_tool(tool_name: str, tool_input: dict, db: Any, client: GraphClient) -> str:
    if tool_name == "create_task":
        due = None
        if tool_input.get("due_date"):
            try:
                due = datetime.fromisoformat(tool_input["due_date"])
            except ValueError:
                pass
        task = Task(
            title=tool_input["title"],
            description=tool_input.get("description"),
            priority=tool_input.get("priority", "medium"),
            due_date=due,
        )
        db.add(task)
        db.flush()
        return f"Created task: '{task.title}' (priority: {task.priority})"

    if tool_name == "add_memory":
        mem = UserMemory(
            content=tool_input["content"],
            memory_type=tool_input.get("memory_type", "fact"),
            source="user_stated",
        )
        db.add(mem)
        db.flush()
        return f"Saved memory: '{mem.content}'"

    if tool_name == "list_tasks":
        status_filter = tool_input.get("status_filter", "all")
        q = db.query(Task)
        if status_filter != "all":
            q = q.filter_by(status=status_filter)
        else:
            q = q.filter(Task.status != "done")
        tasks = q.order_by(Task.created_at.desc()).all()
        if not tasks:
            return "No tasks found."
        lines = [
            f"- [{t.priority}] {t.title} ({t.status})" + (f" due {t.due_date.date()}" if t.due_date else "")
            for t in tasks
        ]
        return "\n".join(lines)

    if tool_name == "search_emails":
        query = tool_input.get("query", "")
        try:
            data = client.get(
                "/me/messages",
                params={
                    "$search": f'"{query}"',
                    "$top": "5",
                    "$select": "subject,from,receivedDateTime,bodyPreview",
                },
            )
            msgs = data.get("value", [])
            if not msgs:
                return "No emails found matching that search."
            lines = []
            for m in msgs:
                sender = m.get("from", {}).get("emailAddress", {}).get("name", "Unknown")
                dt = m.get("receivedDateTime", "")[:10]
                lines.append(f"- [{dt}] {m['subject']} — from {sender}")
            return "\n".join(lines)
        except Exception as e:
            return f"Search failed: {e}"

    return f"Unknown tool: {tool_name}"


def chat(db: Any, graph_client: GraphClient, user_name: str, user_message: str) -> str:
    """
    Send a user message, run Claude with tools, return the assistant's final text.
    Saves user message and assistant response to DB.
    """
    anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # Save user message
    db.add(ChatMessage(role="user", content=user_message))
    db.flush()

    # Load recent history (last 20 messages)
    history = (
        db.query(ChatMessage)
        .order_by(ChatMessage.created_at.desc())
        .limit(20)
        .all()
    )
    history.reverse()

    messages = [{"role": m.role, "content": m.content} for m in history]

    system_prompt = _build_system_prompt(db, user_name)

    # Agentic loop — handle tool calls
    for _iteration in range(10):  # safety cap
        response = anthropic_client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=_TOOLS,
        )

        if response.stop_reason != "tool_use":
            break

        # Collect all tool uses in this response
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        tool_results = []

        for tool_use in tool_uses:
            result_text = _execute_tool(tool_use.name, tool_use.input, db, graph_client)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_text,
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    # Extract final text
    text_blocks = [b for b in response.content if hasattr(b, "text")]
    reply = text_blocks[0].text if text_blocks else "Done."

    # Save assistant response
    db.add(ChatMessage(role="assistant", content=reply))

    return reply


def get_history(db: Any, limit: int = 50) -> list[ChatMessage]:
    return (
        db.query(ChatMessage)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
        .all()
    )


def clear_history(db: Any) -> None:
    db.query(ChatMessage).delete()
