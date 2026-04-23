from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from speemail.models.database import Base


class TrackedEmail(Base):
    __tablename__ = "tracked_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Graph API identifiers (immutable)
    graph_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    graph_conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Classification
    email_type: Mapped[str] = mapped_column(String, nullable=False)
    # 'follow_up' | 'quick_reply'

    status: Mapped[str] = mapped_column(String, nullable=False, default="pending_approval")
    # 'pending_approval' | 'approved' | 'rejected' | 'sent' | 'ai_error'

    # Original email metadata
    original_subject: Mapped[str] = mapped_column(String, nullable=False)
    original_from: Mapped[str] = mapped_column(String, nullable=False)
    original_to: Mapped[str | None] = mapped_column(String, nullable=True)
    original_body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # AI-generated draft
    ai_draft_subject: Mapped[str | None] = mapped_column(String, nullable=True)
    ai_draft_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User edits (set before approve-edited)
    user_edited_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    final_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def effective_body(self) -> str | None:
        """Return user-edited body if available, otherwise AI draft."""
        return self.user_edited_body or self.ai_draft_body

    def confidence_label(self) -> str:
        if self.ai_confidence_score is None:
            return "unknown"
        if self.ai_confidence_score >= 0.90:
            return "high"
        if self.ai_confidence_score >= 0.70:
            return "medium"
        return "low"


class PollCursor(Base):
    __tablename__ = "poll_cursors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cursor_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # 'inbox_quick_reply' | 'sent_follow_up'
    last_checked: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="todo")
    # 'todo' | 'in_progress' | 'done'
    priority: Mapped[str] = mapped_column(String, nullable=False, default="medium")
    # 'high' | 'medium' | 'low'
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    related_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Graph message ID of the incoming email that spawned this task (auto-generated).
    # Used to dedupe so reclassifying the same email doesn't create duplicate tasks.
    source_graph_message_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class UserMemory(Base):
    __tablename__ = "user_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_type: Mapped[str] = mapped_column(String, nullable=False, default="fact")
    # 'fact' | 'preference' | 'project' | 'contact'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="user_stated")
    # 'user_stated' | 'ai_inferred'
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class IgnoreRule(Base):
    __tablename__ = "ignore_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_type: Mapped[str] = mapped_column(String, nullable=False)
    # 'sender' | 'subject'
    pattern: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class EmailClassification(Base):
    """Cached AI verdict for a specific inbox message."""
    __tablename__ = "email_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    graph_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    needs_reply: Mapped[bool] = mapped_column(nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    classified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class WatchedThread(Base):
    """A thread the user is monitoring for new replies."""
    __tablename__ = "watched_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    graph_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    graph_conversation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String, nullable=False, default="")
    recipient: Mapped[str] = mapped_column(String, nullable=False, default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    # source: 'manual_sent' | 'manual_inbox' | 'auto'
    source: Mapped[str] = mapped_column(String, nullable=False, default="manual_sent")
    has_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # AI classification fields (auto-watched threads only)
    ai_expects_reply: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_reasoning: Mapped[str | None] = mapped_column(String, nullable=True)


class SentEmailScan(Base):
    """Cache of AI classification for each sent email (expects reply or not)."""
    __tablename__ = "sent_email_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    graph_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False, default="")
    recipient: Mapped[str] = mapped_column(String, nullable=False, default="")
    body_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expects_reply: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # user_decision: null | 'expects_reply' | 'skip'
    user_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class EmailFeedback(Base):
    """User correction on whether an email needs a reply."""
    __tablename__ = "email_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    graph_message_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    sender_address: Mapped[str] = mapped_column(String, nullable=False)
    sender_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    body_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decision: Mapped[str] = mapped_column(String, nullable=False)
    # 'needs_reply' | 'skip'
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
