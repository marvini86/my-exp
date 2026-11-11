from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.exceptions import ConflictError, NotFoundError
from models.account import Account
from models.account_member import AccountMember, AccountRole
from models.user import User
from repositories.account import AccountRepository
from repositories.account_member import AccountMemberRepository
from schemas.account import AccountResponse, CreateAccountRequest, MyAccountResponse
from services.account_access import require_membership


class AccountService:
    def __init__(
        self,
        session: Session,
        account_repository: AccountRepository,
        member_repository: AccountMemberRepository,
    ) -> None:
        self.session = session
        self.account_repository = account_repository
        self.member_repository = member_repository

    def list_my_accounts(self, actor: User) -> list[MyAccountResponse]:
        return [
            MyAccountResponse.model_validate(
                {**AccountResponse.model_validate(account).model_dump(), "role": role}
            )
            for account, role in self.account_repository.list_for_user(actor.id)
        ]

    def get_account(self, account_id: UUID, actor: User) -> AccountResponse:
        require_membership(self.member_repository, account_id, actor.id)

        account = self.account_repository.get_by_id(account_id)

        if account is None:
            raise NotFoundError("Account not found")

        return AccountResponse.model_validate(account)

    def create_account(self, request: CreateAccountRequest, creator: User) -> AccountResponse:
        """Creates the account and registers the creator as owner, atomically.

        An account never exists without an owner: if any step fails,
        nothing is persisted.
        """
        if self.account_repository.get_by_slug(request.slug) is not None:
            raise ConflictError("Slug already in use")

        account = Account(
            name=request.name,
            slug=request.slug,
            created_by=creator.id,
            updated_by=creator.id,
        )

        try:
            self.account_repository.add(account)
            self.member_repository.add(
                AccountMember(
                    account_id=account.id,
                    user_id=creator.id,
                    role=AccountRole.OWNER,
                )
            )
            self.session.commit()
        except IntegrityError as exc:
            # Race between the check above and the INSERT (slug UNIQUE).
            self.session.rollback()
            raise ConflictError("Slug already in use") from exc

        return AccountResponse.model_validate(account)
