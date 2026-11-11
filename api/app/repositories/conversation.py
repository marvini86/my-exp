from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.conversation import Conversation, Message


class ConversationRepository:
    """Conversations and messages. Conversations are private: always filtered by user."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, conversation: Conversation) -> Conversation:
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def get_owned(self, account_id: UUID, conversation_id: UUID, user_id: UUID) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.account_id == account_id,
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        return self.session.scalars(stmt).one_or_none()

    def list_owned(self, account_id: UUID, user_id: UUID, *, limit: int = 50) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.account_id == account_id, Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())

    def add_message(self, message: Message) -> Message:
        self.session.add(message)
        self.session.flush()
        return message

    def list_messages(self, account_id: UUID, conversation_id: UUID) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.account_id == account_id, Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        )
        return list(self.session.scalars(stmt).all())

    def recent_messages(self, account_id: UUID, conversation_id: UUID, *, limit: int) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.account_id == account_id, Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        return list(reversed(self.session.scalars(stmt).all()))
