"""Account context dependency for `/accounts/{account_id}/...` routes.

Validates that the authenticated user is a member of the account (404 otherwise) and
enables the tenant context on the session, so RLS automatically filters
every table that belongs to the account.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends
from sqlalchemy.orm import Session

from core.auth import get_current_user
from database.connection import get_db
from database.tenant_context import set_account_context
from models.account_member import AccountRole
from models.user import User
from repositories.account_member import AccountMemberRepository
from services.account_access import require_membership


@dataclass(frozen=True)
class AccountContext:
    account_id: UUID
    user: User
    role: AccountRole


def get_account_context(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> AccountContext:
    membership = require_membership(AccountMemberRepository(session), account_id, current_user.id)

    set_account_context(session, account_id, current_user.id)

    return AccountContext(account_id=account_id, user=current_user, role=membership.role)
