from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from core.auth import get_current_user
from core.config import settings
from database.connection import get_db
from models.user import User
from repositories.account import AccountRepository
from repositories.account_member import AccountMemberRepository
from repositories.platform_access import PlatformAccessRepository
from repositories.user import UserRepository
from schemas.account_member import AddMemberRequest, MemberResponse, UpdateMemberRequest
from services.access_policy import AccessPolicy
from services.account_member import AccountMemberService

router = APIRouter(prefix="/accounts/{account_id}/members", tags=["Account members"])


def get_member_service(session: Session = Depends(get_db)) -> AccountMemberService:
    return AccountMemberService(
        session,
        AccountRepository(session),
        AccountMemberRepository(session),
        UserRepository(session),
        AccessPolicy(settings.auth, PlatformAccessRepository(session)),
    )


@router.get("/", response_model=list[MemberResponse])
def list_members(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    service: AccountMemberService = Depends(get_member_service),
) -> list[MemberResponse]:
    return service.list_members(account_id, actor=current_user)


@router.post("/", response_model=MemberResponse, status_code=HTTPStatus.CREATED)
def add_member(
    account_id: UUID,
    request: AddMemberRequest,
    current_user: User = Depends(get_current_user),
    service: AccountMemberService = Depends(get_member_service),
) -> MemberResponse:
    return service.add_member(account_id, request, actor=current_user)


@router.patch("/{user_id}", response_model=MemberResponse)
def update_member_role(
    account_id: UUID,
    user_id: UUID,
    request: UpdateMemberRequest,
    current_user: User = Depends(get_current_user),
    service: AccountMemberService = Depends(get_member_service),
) -> MemberResponse:
    return service.update_member_role(account_id, user_id, request, actor=current_user)


@router.delete("/{user_id}", status_code=HTTPStatus.NO_CONTENT)
def remove_member(
    account_id: UUID,
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    service: AccountMemberService = Depends(get_member_service),
) -> Response:
    service.remove_member(account_id, user_id, actor=current_user)
    return Response(status_code=HTTPStatus.NO_CONTENT)
