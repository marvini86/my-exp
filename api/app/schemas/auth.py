from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, StringConstraints

from schemas.user import UserResponse

# Limits only to reject garbage early; the content is validated in the service.
IdToken = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
OpaqueToken = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class GoogleLoginRequest(BaseModel):
    # ID token (credential) returned by Google Identity Services in the frontend.
    id_token: IdToken


class DevLoginRequest(BaseModel):
    email: EmailStr
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)] | None = None


class RefreshRequest(BaseModel):
    refresh_token: OpaqueToken


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int
    user: UserResponse
