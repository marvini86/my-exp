"""API login, sessions and tokens.

Opaque tokens (random, 256 bits), stored as SHA-256 in
`user_sessions`:
- access token: short-lived (default 15 min), sent in `Authorization: Bearer`;
- refresh token: long-lived (default 30 days, renewed on every use), exchanges the pair.

Opaque instead of JWT: logout and revocation take effect immediately (the session is
looked up on every request, which already happened to load the user) and there is no
signing secret to protect or rotate.

Refresh with rotation: every use issues a new pair and the previous refresh token is
recorded. Presented again within 30 s → 409 (race between parallel
requests; the client retries with the new pair). After that → the session is
revoked (stolen copy).
"""

import hashlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.config import AuthSettings
from core.exceptions import ConflictError, ForbiddenError, UnauthorizedError
from models.user import User
from models.user_session import UserSession
from repositories.platform_access import PlatformAccessRepository
from services.access_policy import AccessPolicy
from repositories.user import UserRepository
from repositories.user_session import UserSessionRepository
from services.google_identity import GoogleIdentity

logger = logging.getLogger(__name__)

GOOGLE_PROVIDER = "google"
ACCESS_TOKEN_PREFIX = "mxa_"
REFRESH_TOKEN_PREFIX = "mxr_"
INVALID_REFRESH = "Invalid refresh token"
REFRESH_ALREADY_ROTATED = "Refresh token already rotated"
# Window for parallel requests that used the same refresh token.
REFRESH_GRACE = timedelta(seconds=30)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token(prefix: str) -> str:
    # The prefix identifies the type (and makes leaked tokens easy to find in logs/repos).
    return prefix + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    user: User


class AuthService:
    def __init__(
        self,
        session: Session,
        user_repository: UserRepository,
        session_repository: UserSessionRepository,
        auth_settings: AuthSettings,
        access_repository: PlatformAccessRepository,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.user_repository = user_repository
        self.session_repository = session_repository
        self.settings = auth_settings
        self.access_repository = access_repository
        self.now = now

    # -- login ------------------------------------------------------------------

    def login_with_google(self, identity: GoogleIdentity, user_agent: str | None) -> IssuedTokens:
        if not self.has_access(identity.email):
            raise ForbiddenError("Email not allowed")

        try:
            user = self._resolve_google_user(identity)
            now = self.now()
            self.session_repository.delete_expired_for_user(user.id, now)
            tokens = self._open_session(user, user_agent, now)
            self.session.commit()
        except IntegrityError as exc:
            # Two simultaneous first logins of the same person (unique indexes).
            self.session.rollback()
            raise ConflictError("Login in progress, try again") from exc

        logger.info("User logged in | user_id=%s provider=%s", user.id, GOOGLE_PROVIDER)
        return tokens

    def login_dev(self, email: str, name: str | None, user_agent: str | None) -> IssuedTokens:
        """DEV ONLY: a real session for an e-mail, without proving identity.

        The route only exists with ENVIRONMENT=development; it lets the frontend
        exercise the token flow (Bearer, refresh, logout) without Google.
        """
        email = email.lower()
        try:
            user = self.user_repository.get_by_email(email) or self.user_repository.add(User(name=name, email=email))
            now = self.now()
            self.session_repository.delete_expired_for_user(user.id, now)
            tokens = self._open_session(user, user_agent, now)
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("Login in progress, try again") from exc

        logger.warning("DEV login | user_id=%s", user.id)
        return tokens

    def has_access(self, email: str | None) -> bool:
        return AccessPolicy(self.settings, self.access_repository).has_access(email)

    def _resolve_google_user(self, identity: GoogleIdentity) -> User:
        user = self.user_repository.get_by_identity(GOOGLE_PROVIDER, identity.subject)
        if user is not None:
            self._sync_profile(user, identity)
            return user

        # Invited user (or registered in dev) with the same e-mail: linked on first sign-in.
        # Safe because Google vouched for the e-mail (email_verified).
        user = self.user_repository.get_by_email(identity.email)
        if user is not None:
            if user.identity_provider is not None:
                raise ConflictError("Email already linked to another login")
            user.identity_provider = GOOGLE_PROVIDER
            user.external_subject = identity.subject
            user.name = user.name or identity.name
            self.session.flush()
            return user

        return self.user_repository.add(
            User(
                name=identity.name,
                email=identity.email,
                identity_provider=GOOGLE_PROVIDER,
                external_subject=identity.subject,
            )
        )

    def _sync_profile(self, user: User, identity: GoogleIdentity) -> None:
        if identity.name and user.name != identity.name:
            user.name = identity.name
        if user.email != identity.email:
            owner = self.user_repository.get_by_email(identity.email)
            # The new e-mail already belongs to another user: keep the current one.
            if owner is None:
                user.email = identity.email
        self.session.flush()

    # -- sessions ----------------------------------------------------------------

    def _open_session(self, user: User, user_agent: str | None, now: datetime) -> IssuedTokens:
        access, refresh = _new_token(ACCESS_TOKEN_PREFIX), _new_token(REFRESH_TOKEN_PREFIX)
        self.session_repository.add(
            UserSession(
                user_id=user.id,
                access_token_hash=hash_token(access),
                access_expires_at=now + self._access_ttl,
                refresh_token_hash=hash_token(refresh),
                refresh_expires_at=now + self._refresh_ttl,
                user_agent=(user_agent or None) and user_agent[:255],
            )
        )
        return self._issued(access, refresh, user)

    def refresh(self, refresh_token: str) -> IssuedTokens:
        token_hash = hash_token(refresh_token)
        user_session = self.session_repository.get_by_refresh_hash_for_update(token_hash)
        now = self.now()

        if user_session is None or user_session.revoked_at is not None:
            raise UnauthorizedError(INVALID_REFRESH)

        if user_session.previous_refresh_token_hash == token_hash:
            grace = user_session.previous_refresh_grace_until
            if grace is not None and now <= grace:
                # Legitimate race: another request has just refreshed. The client
                # should retry with the new refresh token (cookie already updated).
                raise ConflictError(REFRESH_ALREADY_ROTATED)
            # An already rotated refresh token showed up again: someone has a copy.
            user_session.revoked_at = now
            self.session.commit()
            logger.warning("Refresh token reuse detected | session_id=%s", user_session.id)
            raise UnauthorizedError(INVALID_REFRESH)

        if user_session.refresh_expires_at <= now:
            raise UnauthorizedError(INVALID_REFRESH)

        # Access revoked (admin removed the e-mail, left every account):
        # the session ends on the next refresh, within one access token (15 min).
        if not self.has_access(user_session.user.email):
            user_session.revoked_at = now
            self.session.commit()
            logger.info("Session revoked: access removed | user_id=%s", user_session.user_id)
            raise UnauthorizedError(INVALID_REFRESH)

        access, refresh = _new_token(ACCESS_TOKEN_PREFIX), _new_token(REFRESH_TOKEN_PREFIX)
        user_session.previous_refresh_token_hash = token_hash
        user_session.previous_refresh_grace_until = now + REFRESH_GRACE
        user_session.refresh_token_hash = hash_token(refresh)
        user_session.refresh_expires_at = now + self._refresh_ttl
        user_session.access_token_hash = hash_token(access)
        user_session.access_expires_at = now + self._access_ttl
        self.session.commit()

        return self._issued(access, refresh, user_session.user)

    def authenticate(self, access_token: str) -> UserSession | None:
        """Valid session that owns the access token, or None (expired, revoked, missing)."""
        user_session = self.session_repository.get_by_access_hash(hash_token(access_token))
        if (
            user_session is None
            or user_session.revoked_at is not None
            or user_session.access_expires_at <= self.now()
        ):
            return None
        return user_session

    def logout(self, user_session: UserSession, *, everywhere: bool = False) -> None:
        now = self.now()
        if everywhere:
            self.session_repository.revoke_all_for_user(user_session.user_id, now)
        else:
            user_session.revoked_at = now
        self.session.commit()

    # -- auxiliares -------------------------------------------------------------

    @property
    def _access_ttl(self) -> timedelta:
        return timedelta(seconds=self.settings.access_token_ttl_seconds)

    @property
    def _refresh_ttl(self) -> timedelta:
        return timedelta(days=self.settings.refresh_token_ttl_days)

    def _issued(self, access: str, refresh: str, user: User) -> IssuedTokens:
        return IssuedTokens(
            access_token=access,
            refresh_token=refresh,
            expires_in=int(self._access_ttl.total_seconds()),
            refresh_expires_in=int(self._refresh_ttl.total_seconds()),
            user=user,
        )
