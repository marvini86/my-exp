import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.mixins.timestamped import Timestamped
from database.mixins.uuid_primary_key import UUIDPrimaryKey


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Conversation(UUIDPrimaryKey, Timestamped, Base):
    """A user's conversation with the assistant, within an account. RLS-protected table."""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("account_id", "id", name="uq_conversations_account_id"),)

    account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))


class Message(UUIDPrimaryKey, Base):
    """Message of a conversation (no updated_at: messages are never edited). RLS-protected table."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "conversation_id"],
            ["conversations.account_id", "conversations.id"],
            name="fk_messages_conversation",
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    role: Mapped[MessageRole] = mapped_column(
        Enum(
            MessageRole,
            native_enum=False,
            create_constraint=False,
            length=30,
            values_callable=lambda roles: [role.value for role in roles],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Set by the application (microseconds): PostgreSQL NOW() is the start of the
    # transaction, and messages written in the same transaction would tie in ordering.
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
