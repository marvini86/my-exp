"""Identification of the authenticated user.

Production: `Authorization: Bearer <access token>` issued by `/auth/google`
or `/auth/refresh` (see services/auth.py).

Development (ENVIRONMENT=development): without a Bearer token, the
`X-Dev-User-Id` header with the user's UUID is also accepted. This is not security (any
client can send any UUID); in production it is ignored.

Routes depend only on `get_current_user` (and `get_current_session` on logout).
"""

from uuid import UUID

from fastapi import Depends
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from core.config import settings
from core.exceptions import NotFoundError, UnauthorizedError
from database.connection import get_db
from models.user import User
from models.user_session import UserSession
from repositories.platform_access import PlatformAccessRepository
from repositories.user import UserRepository
from repositories.user_session import UserSessionRepository
from services.auth import AuthService

DEV_USER_HEADER = "X-Dev-User-Id"
NOT_AUTHENTICATED = "Not authenticated"

# Both make Swagger (/docs) show the "Authorize" button.
bearer_scheme = HTTPBearer(
    scheme_name="Bearer",
    description="Access token de /auth/google ou /auth/refresh.",
    auto_error=False,
)
dev_user_header = APIKeyHeader(
    name=DEV_USER_HEADER,
    scheme_name="DevUser",
    description="DEV ONLY (ENVIRONMENT=development): the user's UUID.",
    auto_error=False,
)


def require_development() -> None:
    """Development routes "do not exist" outside ENVIRONMENT=development."""
    if settings.environment != "development":
        raise NotFoundError("Not Found")


def get_auth_service(session: Session = Depends(get_db)) -> AuthService:
    return AuthService(
        session,
        UserRepository(session),
        UserSessionRepository(session),
        settings.auth,
        PlatformAccessRepository(session),
    )


def _session_from_bearer(credentials: HTTPAuthorizationCredentials | None, service: AuthService) -> UserSession | None:
    if credentials is None:
        return None
    user_session = service.authenticate(credentials.credentials)
    if user_session is None:
        # Token sent but invalid/expired: 401 (the client should use the refresh token).
        raise UnauthorizedError(NOT_AUTHENTICATED)
    return user_session


def get_current_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    service: AuthService = Depends(get_auth_service),
) -> UserSession:
    user_session = _session_from_bearer(credentials, service)
    if user_session is None:
        raise UnauthorizedError(NOT_AUTHENTICATED)
    return user_session


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    raw_dev_user_id: str | None = Depends(dev_user_header),
    service: AuthService = Depends(get_auth_service),
    session: Session = Depends(get_db),
) -> User:
    user_session = _session_from_bearer(credentials, service)
    if user_session is not None:
        return user_session.user

    if settings.environment == "development" and raw_dev_user_id:
        return _dev_user(raw_dev_user_id, session)

    raise UnauthorizedError(NOT_AUTHENTICATED)


def _dev_user(raw_user_id: str, session: Session) -> User:
    try:
        user_id = UUID(raw_user_id)
    except ValueError:
        raise UnauthorizedError(NOT_AUTHENTICATED) from None

    user = UserRepository(session).get_by_id(user_id)

    # Same response for an invalid UUID and a non-existent user:
    # does not reveal which IDs exist.
    if user is None:
        raise UnauthorizedError(NOT_AUTHENTICATED)

    return user


def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    """Platform administrator (AUTH_ADMIN_EMAILS). For everyone else the route "does not exist"."""
    if not settings.auth.is_admin(user.email):
        raise NotFoundError("Not Found")
    return user
