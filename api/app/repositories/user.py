from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models.account import Account
from models.account_member import AccountMember
from models.user import User


class UserRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, user_id: UUID) -> User | None:
        return self.session.get(User, user_id)

    def get_by_identity(self, provider: str, subject: str) -> User | None:
        stmt = select(User).where(User.identity_provider == provider, User.external_subject == subject)
        return self.session.scalars(stmt).one_or_none()

    def get_by_email(self, email: str) -> User | None:
        # lower(email) uses the unique index uq_users_email_lower.
        stmt = select(User).where(func.lower(User.email) == email.lower())
        return self.session.scalars(stmt).one_or_none()

    def _visible_to(self, viewer_id: UUID):
        """Condition: the user themselves or anyone sharing an active account with them."""
        viewer_accounts = (
            select(AccountMember.account_id)
            .join(Account, Account.id == AccountMember.account_id)
            .where(AccountMember.user_id == viewer_id, Account.deleted_at.is_(None))
        )
        co_members = select(AccountMember.user_id).where(
            AccountMember.account_id.in_(viewer_accounts)
        )
        return or_(User.id == viewer_id, User.id.in_(co_members))

    def get_visible(self, user_id: UUID, viewer_id: UUID) -> User | None:
        stmt = select(User).where(User.id == user_id, self._visible_to(viewer_id))
        return self.session.scalars(stmt).one_or_none()

    def list_visible(self, viewer_id: UUID) -> list[User]:
        stmt = select(User).where(self._visible_to(viewer_id)).order_by(User.created_at)
        return list(self.session.scalars(stmt).all())

    def add(self, user: User) -> User:
        self.session.add(user)
        self.session.flush()
        return user
