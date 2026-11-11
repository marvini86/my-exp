from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints

UserName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class UserResponse(BaseModel):
    id: UUID
    name: str | None
    email: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeResponse(UserResponse):
    # Administers the platform (AUTH_ADMIN_EMAILS): sees the access screen.
    is_platform_admin: bool
    # Can invite people to accounts (admin or allowed by the admin; invited users cannot).
    can_invite: bool


class CreateUserRequest(BaseModel):
    name: UserName
    email: EmailStr
