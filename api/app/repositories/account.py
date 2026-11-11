from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.account import Account
from models.account_member import AccountMember, AccountRole


class AccountRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, account_id: UUID) -> Account | None:
        stmt = select(Account).where(
            Account.id == account_id,
            Account.deleted_at.is_(None),
        )
        return self.session.scalars(stmt).one_or_none()

    def get_by_id_for_update(self, account_id: UUID) -> Account | None:
        """Locks the account row until the end of the transaction.

        Serializes concurrent changes to the members of the same account
        (e.g. two owners demoting themselves at the same time).
        """
        stmt = (
            select(Account)
            .where(Account.id == account_id, Account.deleted_at.is_(None))
            .with_for_update()
        )
        return self.session.scalars(stmt).one_or_none()

    def get_by_slug(self, slug: str) -> Account | None:
        # Includes soft-deleted accounts: the slug stays reserved (UNIQUE in the database).
        stmt = select(Account).where(Account.slug == slug)
        return self.session.scalars(stmt).one_or_none()

    def list_for_user(self, user_id: UUID) -> list[tuple[Account, AccountRole]]:
        stmt = (
            select(Account, AccountMember.role)
            .join(AccountMember, AccountMember.account_id == Account.id)
            .where(
                AccountMember.user_id == user_id,
                Account.deleted_at.is_(None),
            )
            .order_by(Account.name)
        )
        return [(account, role) for account, role in self.session.execute(stmt).all()]

    def add(self, account: Account) -> Account:
        self.session.add(account)
        self.session.flush()
        return account
