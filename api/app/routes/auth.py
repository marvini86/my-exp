from http import HTTPStatus

from fastapi import APIRouter, Depends, Request, Response

from core.auth import get_auth_service, get_current_session, require_development
from core.rate_limit import AUTH_LOGIN, AUTH_REFRESH, rate_limit
from models.user_session import UserSession
from schemas.auth import DevLoginRequest, GoogleLoginRequest, RefreshRequest, TokenResponse
from schemas.user import UserResponse
from services.auth import AuthService, IssuedTokens
from services.google_identity import GoogleTokenVerifier, get_google_verifier

router = APIRouter(prefix="/auth", tags=["Auth"])


def _response(tokens: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        refresh_expires_in=tokens.refresh_expires_in,
        user=UserResponse.model_validate(tokens.user),
    )


@router.post("/google", response_model=TokenResponse, dependencies=[Depends(rate_limit(AUTH_LOGIN))])
def login_with_google(
    body: GoogleLoginRequest,
    request: Request,
    verifier: GoogleTokenVerifier = Depends(get_google_verifier),
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Exchanges the Google ID token for an API session.

    The first login creates the user (or links the invitation with the same e-mail).
    """
    identity = verifier.verify(body.id_token)
    return _response(service.login_with_google(identity, request.headers.get("user-agent")))


@router.post(
    "/dev-login",
    response_model=TokenResponse,
    dependencies=[Depends(require_development), Depends(rate_limit(AUTH_LOGIN))],
)
def dev_login(
    body: DevLoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """DEV ONLY (404 outside development): same tokens as the Google login, by e-mail only."""
    return _response(service.login_dev(body.email, body.name, request.headers.get("user-agent")))


@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(rate_limit(AUTH_REFRESH))])
def refresh(body: RefreshRequest, service: AuthService = Depends(get_auth_service)) -> TokenResponse:
    """Issues a new token pair; the refresh token sent is no longer valid."""
    return _response(service.refresh(body.refresh_token))


@router.post("/logout", status_code=HTTPStatus.NO_CONTENT)
def logout(
    everywhere: bool = False,
    user_session: UserSession = Depends(get_current_session),
    service: AuthService = Depends(get_auth_service),
) -> Response:
    """Ends the current session (or all of them, with `everywhere=true`)."""
    service.logout(user_session, everywhere=everywhere)
    return Response(status_code=HTTPStatus.NO_CONTENT)
