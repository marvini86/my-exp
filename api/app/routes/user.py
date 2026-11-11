from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.auth import get_current_user, require_development
from core.config import settings
from repositories.platform_access import PlatformAccessRepository
from services.access_policy import AccessPolicy
from database.connection import get_db
from models.user import User
from repositories.user import UserRepository
from schemas.user import CreateUserRequest, MeResponse, UserResponse
from services.user import UserService

router = APIRouter(prefix="/users", tags=["Users"])


def get_user_service(session: Session = Depends(get_db)) -> UserService:
    return UserService(session, UserRepository(session))


@router.get("/", response_model=list[UserResponse])
def list_users(
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> list[UserResponse]:
    """Users who share at least one account with the current user."""
    return service.list_users(viewer=current_user)


# Declared before "/{user_id}" so "me" is not parsed as a UUID.
@router.get("/me", response_model=MeResponse)
def get_me(
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
    session: Session = Depends(get_db),
) -> MeResponse:
    me = service.get_user(current_user.id, viewer=current_user)
    policy = AccessPolicy(settings.auth, PlatformAccessRepository(session))
    return MeResponse(
        **me.model_dump(),
        is_platform_admin=settings.auth.is_admin(current_user.email),
        can_invite=policy.can_invite(current_user.email),
    )


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    return service.get_user(user_id, viewer=current_user)


# DEV ONLY: sign-up without login, to be used with the X-Dev-User-Id header.
@router.post(
    "/",
    response_model=UserResponse,
    status_code=HTTPStatus.CREATED,
    dependencies=[Depends(require_development)],
)
def create_user(request: CreateUserRequest, service: UserService = Depends(get_user_service)) -> UserResponse:
    return service.create_user(request)
