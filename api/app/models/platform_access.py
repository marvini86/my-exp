from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.mixins.timestamped import Timestamped
from database.mixins.uuid_primary_key import UUIDPrimaryKey


class PlatformAccess(UUIDPrimaryKey, Timestamped, Base):
    """E-mail allowed by the platform administrator to sign in with Google."""

    __tablename__ = "platform_access"

    email: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
