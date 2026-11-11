from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class GrantAccessRequest(BaseModel):
    email: EmailStr


class AccessEntryResponse(BaseModel):
    id: UUID
    email: str
    created_at: datetime
    # Has signed in with Google at least once.
    registered: bool
    name: str | None
