"""Account access rules, shared between services."""

from uuid import UUID

from core.exceptions import ForbiddenError, NotFoundError
from models.account_member import AccountMember, AccountRole
from repositories.account_member import AccountMemberRepository


def require_membership(
    member_repository: AccountMemberRepository,
    account_id: UUID,
    user_id: UUID,
) -> AccountMember:
    """Returns the user's membership in the account.

    Non-members get 404 (not 403) so the account's existence is not revealed.
    """
    membership = member_repository.get_active_membership(account_id, user_id)

    if membership is None:
        raise NotFoundError("Account not found")

    return membership


def require_role(role: AccountRole, *allowed: AccountRole) -> None:
    if role not in allowed:
        raise ForbiddenError("Insufficient permissions")


def require_member_management(actor: AccountMember, *affected_roles: AccountRole) -> None:
    """Checks whether `actor` may manage members with the affected roles.

    - owner: manages any role;
    - admin: manages only `member` (does not promote or change admins/owners);
    - member: manages nobody.
    """
    if actor.role is AccountRole.OWNER:
        return

    if actor.role is AccountRole.ADMIN and all(role is AccountRole.MEMBER for role in affected_roles):
        return

    raise ForbiddenError("Insufficient permissions")
