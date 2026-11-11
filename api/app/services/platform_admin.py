"""Platform administration: who can sign in (besides people invited to accounts)."""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.exceptions import ConflictError, NotFoundError
from models.platform_access import PlatformAccess
from models.user import User
from repositories.platform_access import PlatformAccessRepository
from schemas.admin import AccessEntryResponse, GrantAccessRequest


class PlatformAdminService:
    def __init__(self, session: Session, repository: PlatformAccessRepository) -> None:
        self.session = session
        self.repository = repository

    def list_access(self) -> list[AccessEntryResponse]:
        return [
            AccessEntryResponse(
                id=access.id,
                email=access.email,
                created_at=access.created_at,
                registered=bool(user and user.identity_provider),
                name=user.name if user else None,
            )
            for access, user in self.repository.list_with_users()
        ]

    def grant(self, request: GrantAccessRequest, admin: User) -> AccessEntryResponse:
        email = request.email.lower()
        if self.repository.is_listed(email):
            raise ConflictError("Email already allowed")
        try:
            access = self.repository.add(PlatformAccess(email=email, created_by=admin.id))
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("Email already allowed") from exc
        return AccessEntryResponse(id=access.id, email=email, created_at=access.created_at, registered=False, name=None)

    def revoke(self, access_id: UUID) -> None:
        """Removes the allowance. Anyone who is also a member of some account still signs in through it."""
        access = self.repository.get(access_id)
        if access is None:
            raise NotFoundError("Access not found")
        self.repository.delete(access)
        self.session.commit()
