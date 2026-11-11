from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from models.account import Account
from models.account_member import AccountMember, AccountRole


class AccountMemberRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, account_id: UUID, user_id: UUID) -> AccountMember | None:
        stmt = (
            select(AccountMember)
            .options(joinedload(AccountMember.user))
            .where(
                AccountMember.account_id == account_id,
                AccountMember.user_id == user_id,
            )
        )
        return self.session.scalars(stmt).one_or_none()

    def get_active_membership(self, account_id: UUID, user_id: UUID) -> AccountMember | None:
        """The user's membership in a non-deleted account."""
        stmt = (
            select(AccountMember)
            .join(Account, Account.id == AccountMember.account_id)
            .where(
                AccountMember.account_id == account_id,
                AccountMember.user_id == user_id,
                Account.deleted_at.is_(None),
            )
        )
        return self.session.scalars(stmt).one_or_none()

    def list_by_account(self, account_id: UUID) -> list[AccountMember]:
        stmt = (
            select(AccountMember)
            .options(joinedload(AccountMember.user))
            .where(AccountMember.account_id == account_id)
            .order_by(AccountMember.created_at)
        )
        return list(self.session.scalars(stmt).all())

    def count_owners(self, account_id: UUID) -> int:
        stmt = select(func.count()).where(
            AccountMember.account_id == account_id,
            AccountMember.role == AccountRole.OWNER,
        )
        return self.session.scalar(stmt) or 0

    def add(self, member: AccountMember) -> AccountMember:
        self.session.add(member)
        self.session.flush()
        return member

    def delete(self, member: AccountMember) -> None:
        self.session.delete(member)
        self.session.flush()
