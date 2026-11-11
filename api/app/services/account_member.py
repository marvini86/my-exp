from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.exceptions import ConflictError, ForbiddenError, NotFoundError
from models.account_member import AccountMember, AccountRole
from models.user import User
from repositories.account import AccountRepository
from repositories.account_member import AccountMemberRepository
from repositories.user import UserRepository
from schemas.account_member import AddMemberRequest, MemberResponse, UpdateMemberRequest
from services.access_policy import AccessPolicy
from services.account_access import require_member_management, require_membership

LAST_OWNER_MESSAGE = "Account must have at least one owner"
INVITE_NOT_ALLOWED = "Only people granted by the platform admin can invite"


class AccountMemberService:
    def __init__(
        self,
        session: Session,
        account_repository: AccountRepository,
        member_repository: AccountMemberRepository,
        user_repository: UserRepository,
        access_policy: AccessPolicy | None = None,
    ) -> None:
        self.session = session
        self.account_repository = account_repository
        self.member_repository = member_repository
        self.user_repository = user_repository
        self.access_policy = access_policy

    def list_members(self, account_id: UUID, actor: User) -> list[MemberResponse]:
        require_membership(self.member_repository, account_id, actor.id)

        return [
            self._to_response(member)
            for member in self.member_repository.list_by_account(account_id)
        ]

    def add_member(self, account_id: UUID, request: AddMemberRequest, actor: User) -> MemberResponse:
        actor_membership = require_membership(self.member_repository, account_id, actor.id)
        require_member_management(actor_membership, request.role)
        # Invited users do not invite: only the admin and whoever they allowed (services/access_policy.py).
        if self.access_policy is not None and not self.access_policy.can_invite(actor.email):
            raise ForbiddenError(INVITE_NOT_ALLOWED)

        email = request.email.lower()
        user = self.user_repository.get_by_email(email)

        if user is not None and self.member_repository.get(account_id, user.id) is not None:
            raise ConflictError("User is already a member")

        try:
            if user is None:
                # Invitation: a user with only an e-mail, linked on the first Google login
                # with this verified e-mail (services/auth.py).
                user = self.user_repository.add(User(email=email))

            member = AccountMember(account_id=account_id, user_id=user.id, role=request.role)
            self.member_repository.add(member)
            self.session.commit()
        except IntegrityError as exc:
            # Race with another insertion of the same user (uq_account_member).
            self.session.rollback()
            raise ConflictError("User is already a member") from exc

        member.user = user
        return self._to_response(member)

    def update_member_role(
        self,
        account_id: UUID,
        user_id: UUID,
        request: UpdateMemberRequest,
        actor: User,
    ) -> MemberResponse:
        self._lock_account(account_id)
        actor_membership = require_membership(self.member_repository, account_id, actor.id)
        target = self._get_target(account_id, user_id)

        require_member_management(actor_membership, target.role, request.role)

        if target.role is AccountRole.OWNER and request.role is not AccountRole.OWNER:
            self._ensure_not_last_owner(account_id)

        target.role = request.role
        self.session.commit()

        return self._to_response(target)

    def remove_member(self, account_id: UUID, user_id: UUID, actor: User) -> None:
        self._lock_account(account_id)
        actor_membership = require_membership(self.member_repository, account_id, actor.id)
        target = self._get_target(account_id, user_id)

        # Any member can leave the account; removing someone else requires permission.
        if target.user_id != actor.id:
            require_member_management(actor_membership, target.role)

        if target.role is AccountRole.OWNER:
            self._ensure_not_last_owner(account_id)

        self.member_repository.delete(target)
        self.session.commit()

    def _lock_account(self, account_id: UUID) -> None:
        # No account (or deleted): same response as a non-member, 404.
        if self.account_repository.get_by_id_for_update(account_id) is None:
            raise NotFoundError("Account not found")

    def _get_target(self, account_id: UUID, user_id: UUID) -> AccountMember:
        target = self.member_repository.get(account_id, user_id)

        if target is None:
            raise NotFoundError("Member not found")

        return target

    def _ensure_not_last_owner(self, account_id: UUID) -> None:
        if self.member_repository.count_owners(account_id) <= 1:
            raise ConflictError(LAST_OWNER_MESSAGE)

    @staticmethod
    def _to_response(member: AccountMember) -> MemberResponse:
        return MemberResponse(
            user_id=member.user_id,
            name=member.user.name,
            email=member.user.email,
            role=member.role,
            joined_at=member.created_at,
            pending=member.user.identity_provider is None,
        )
