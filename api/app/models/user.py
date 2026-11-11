from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base
from database.mixins.timestamped import Timestamped
from database.mixins.uuid_primary_key import UUIDPrimaryKey

if TYPE_CHECKING:
    from models.account_member import AccountMember


class User(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "users"

    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))

    # Login provider and token "sub". Null = invited user who has not signed in yet.
    identity_provider: Mapped[str | None] = mapped_column(String(50))
    external_subject: Mapped[str | None] = mapped_column(String(255))

    memberships: Mapped[list["AccountMember"]] = relationship(
        back_populates="user",
    )
