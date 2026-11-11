from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints

from models.account_member import AccountRole

AccountName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]

def _normalize_slug(value: object) -> object:
    # Normalizes before `pattern`, which Pydantic evaluates before `to_lower`.
    return value.strip().lower() if isinstance(value, str) else value


# Lowercase letters, digits and single hyphens: "familia-silva".
AccountSlug = Annotated[
    str,
    BeforeValidator(_normalize_slug),
    StringConstraints(
        min_length=3,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]


class AccountResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MyAccountResponse(AccountResponse):
    """Account as seen by the current user, with their role in it."""
    role: AccountRole


class CreateAccountRequest(BaseModel):
    # `active` is not accepted from the client: every account starts active.
    name: AccountName
    slug: AccountSlug
