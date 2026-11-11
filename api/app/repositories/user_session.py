from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from models.user_session import UserSession


class UserSessionRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_access_hash(self, token_hash: str) -> UserSession | None:
        stmt = select(UserSession).where(UserSession.access_token_hash == token_hash)
        return self.session.scalars(stmt).one_or_none()

    def get_by_refresh_hash_for_update(self, token_hash: str) -> UserSession | None:
        """Locks the row: two simultaneous refreshes of the same token do not rotate twice."""
        stmt = (
            select(UserSession)
            .where(
                or_(
                    UserSession.refresh_token_hash == token_hash,
                    UserSession.previous_refresh_token_hash == token_hash,
                )
            )
            .with_for_update(of=UserSession)
        )
        return self.session.scalars(stmt).one_or_none()

    def add(self, user_session: UserSession) -> UserSession:
        self.session.add(user_session)
        self.session.flush()
        return user_session

    def revoke_all_for_user(self, user_id: UUID, now: datetime) -> None:
        self.session.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    def delete_expired_for_user(self, user_id: UUID, now: datetime) -> None:
        """Cheap cleanup on every login: sessions that are no longer useful."""
        self.session.execute(
            delete(UserSession).where(
                UserSession.user_id == user_id,
                or_(UserSession.refresh_expires_at < now, UserSession.revoked_at.is_not(None)),
            )
        )
