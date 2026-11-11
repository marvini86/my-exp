from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base
from database.mixins.timestamped import Timestamped
from database.mixins.uuid_primary_key import UUIDPrimaryKey
from models.user import User


class UserSession(UUIDPrimaryKey, Timestamped, Base):
    """Login session. Only the SHA-256 of the tokens is persisted."""

    __tablename__ = "user_sessions"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    access_token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    access_expires_at: Mapped[datetime]

    refresh_token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    refresh_expires_at: Mapped[datetime]
    previous_refresh_token_hash: Mapped[str | None] = mapped_column(CHAR(64), unique=True)
    previous_refresh_grace_until: Mapped[datetime | None]

    user_agent: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(lazy="joined")
