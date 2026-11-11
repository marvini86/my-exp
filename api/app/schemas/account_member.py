from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from models.account_member import AccountRole


class MemberResponse(BaseModel):
    user_id: UUID
    name: str | None
    email: str | None
    role: AccountRole
    joined_at: datetime
    # Invited user who has not logged in yet.
    pending: bool


class AddMemberRequest(BaseModel):
    # An e-mail with no account becomes an invitation: the membership applies on the first Google login.
    email: EmailStr
    role: AccountRole = AccountRole.MEMBER


class UpdateMemberRequest(BaseModel):
    role: AccountRole
