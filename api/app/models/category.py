import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.mixins.timestamped import Timestamped
from database.mixins.uuid_primary_key import UUIDPrimaryKey


class Category(UUIDPrimaryKey, Timestamped, Base):
    """Financial category of an account. RLS-protected table."""

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_category_account_id"),
        UniqueConstraint("account_id", "name", name="uq_category_name"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
