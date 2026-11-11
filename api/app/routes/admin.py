from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from core.auth import require_platform_admin
from database.connection import get_db
from models.user import User
from repositories.platform_access import PlatformAccessRepository
from schemas.admin import AccessEntryResponse, GrantAccessRequest
from services.platform_admin import PlatformAdminService

router = APIRouter(prefix="/admin", tags=["Admin"])


def get_admin_service(session: Session = Depends(get_db)) -> PlatformAdminService:
    return PlatformAdminService(session, PlatformAccessRepository(session))


@router.get("/access", response_model=list[AccessEntryResponse])
def list_access(
    _: User = Depends(require_platform_admin),
    service: PlatformAdminService = Depends(get_admin_service),
) -> list[AccessEntryResponse]:
    """E-mails allowed to sign in with Google (platform administrators only)."""
    return service.list_access()


@router.post("/access", response_model=AccessEntryResponse, status_code=HTTPStatus.CREATED)
def grant_access(
    request: GrantAccessRequest,
    admin: User = Depends(require_platform_admin),
    service: PlatformAdminService = Depends(get_admin_service),
) -> AccessEntryResponse:
    return service.grant(request, admin)


@router.delete("/access/{access_id}", status_code=HTTPStatus.NO_CONTENT)
def revoke_access(
    access_id: UUID,
    _: User = Depends(require_platform_admin),
    service: PlatformAdminService = Depends(get_admin_service),
) -> Response:
    service.revoke(access_id)
    return Response(status_code=HTTPStatus.NO_CONTENT)
