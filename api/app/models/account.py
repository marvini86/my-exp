from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base
from database.mixins.activatable import Activatable
from database.mixins.auditable import Auditable
from database.mixins.soft_deletable import SoftDeletable
from database.mixins.timestamped import Timestamped
from database.mixins.uuid_primary_key import UUIDPrimaryKey

if TYPE_CHECKING:
    from models.account_member import AccountMember


class Account(
    UUIDPrimaryKey,
    Activatable,
    Auditable,
    SoftDeletable,
    Timestamped,
    Base,
):
    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    members: Mapped[list["AccountMember"]] = relationship(
        back_populates="account",
    )
