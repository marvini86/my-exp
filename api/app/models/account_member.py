import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base
from database.mixins.timestamped import Timestamped
from database.mixins.uuid_primary_key import UUIDPrimaryKey

if TYPE_CHECKING:
    from models.account import Account
    from models.user import User


class AccountRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class AccountMember(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "account_members"
    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_account_member"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id"),
        nullable=False,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    # VARCHAR + CHECK in the database (ck_account_member_role); no native enum.
    role: Mapped[AccountRole] = mapped_column(
        Enum(
            AccountRole,
            native_enum=False,
            create_constraint=False,
            length=30,
            values_callable=lambda roles: [role.value for role in roles],
        ),
        nullable=False,
        default=AccountRole.MEMBER,
        server_default=AccountRole.MEMBER.value,
    )

    account: Mapped["Account"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")
