from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from models.account import Account
from models.account_member import AccountMember
from models.platform_access import PlatformAccess
from models.user import User


class PlatformAccessRepository:
    def __init__(self, session: Session):
        self.session = session

    def is_listed(self, email: str) -> bool:
        return bool(self.session.scalar(select(exists().where(func.lower(PlatformAccess.email) == email.lower()))))

    def is_member_somewhere(self, email: str) -> bool:
        """Invited to (or owner of) some active account — members can invite anyone."""
        stmt = select(
            exists()
            .where(func.lower(User.email) == email.lower())
            .where(AccountMember.user_id == User.id)
            .where(Account.id == AccountMember.account_id, Account.deleted_at.is_(None))
        )
        return bool(self.session.scalar(stmt))

    def list_with_users(self) -> list[tuple[PlatformAccess, User | None]]:
        stmt = (
            select(PlatformAccess, User)
            .outerjoin(User, func.lower(User.email) == func.lower(PlatformAccess.email))
            .order_by(PlatformAccess.created_at.desc())
        )
        return [(access, user) for access, user in self.session.execute(stmt).all()]

    def get(self, access_id: UUID) -> PlatformAccess | None:
        return self.session.get(PlatformAccess, access_id)

    def add(self, access: PlatformAccess) -> PlatformAccess:
        self.session.add(access)
        self.session.flush()
        return access

    def delete(self, access: PlatformAccess) -> None:
        self.session.delete(access)
        self.session.flush()
