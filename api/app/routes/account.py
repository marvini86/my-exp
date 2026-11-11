from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.auth import get_current_user
from database.connection import get_db
from models.user import User
from repositories.account import AccountRepository
from repositories.account_member import AccountMemberRepository
from schemas.account import AccountResponse, CreateAccountRequest, MyAccountResponse
from services.account import AccountService

router = APIRouter(prefix="/accounts", tags=["Accounts"])


def get_account_service(session: Session = Depends(get_db)) -> AccountService:
    return AccountService(
        session,
        AccountRepository(session),
        AccountMemberRepository(session),
    )


# Declared before "/{account_id}" so "me" is not parsed as a UUID.
@router.get("/me", response_model=list[MyAccountResponse])
def list_my_accounts(
    current_user: User = Depends(get_current_user),
    service: AccountService = Depends(get_account_service),
) -> list[MyAccountResponse]:
    return service.list_my_accounts(current_user)


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    return service.get_account(account_id, actor=current_user)


@router.post("/", response_model=AccountResponse, status_code=HTTPStatus.CREATED)
def create_account(
    request: CreateAccountRequest,
    current_user: User = Depends(get_current_user),
    service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    return service.create_account(request, creator=current_user)
