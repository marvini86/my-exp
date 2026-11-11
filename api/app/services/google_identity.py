"""Google ID token validation (Google Identity Services).

The frontend signs in with Google and sends the ID token (JWT RS256) to the
API. Here it is validated locally against Google's public keys:
signature, `aud` (our Client ID), `iss`, `exp`/`iat` and verified e-mail.
"""

import logging
import ssl
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt

from core.config import settings
from core.exceptions import ServiceUnavailableError, UnauthorizedError

logger = logging.getLogger(__name__)

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
# Clock skew tolerance between the server and Google.
CLOCK_LEEWAY_SECONDS = 30

INVALID_TOKEN = "Invalid Google token"

KeyResolver = Callable[[str], Any]


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    name: str | None


class GoogleTokenVerifier:
    def __init__(self, client_ids: Sequence[str], key_resolver: KeyResolver) -> None:
        self.client_ids = list(client_ids)
        self.key_resolver = key_resolver

    @classmethod
    def from_google(cls, client_ids: Sequence[str]) -> "GoogleTokenVerifier":
        # Cached keys (Google rotates them rarely); OS certificates.
        jwks = jwt.PyJWKClient(
            GOOGLE_CERTS_URL,
            cache_keys=True,
            lifespan=3600,
            timeout=10,
            ssl_context=ssl.create_default_context(),
        )
        return cls(client_ids, lambda token: jwks.get_signing_key_from_jwt(token).key)

    def verify(self, id_token: str) -> GoogleIdentity:
        if not self.client_ids:
            raise ServiceUnavailableError("Google login is not configured")

        try:
            key = self.key_resolver(id_token)
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self.client_ids,
                issuer=GOOGLE_ISSUERS,
                leeway=CLOCK_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWKClientConnectionError as exc:
            logger.warning("Google JWKS unavailable | error=%s", type(exc).__name__)
            raise ServiceUnavailableError("Google login temporarily unavailable") from exc
        except jwt.PyJWTError as exc:
            # Error type only: the token is a credential and does not go to the log.
            logger.info("Google token rejected | error=%s", type(exc).__name__)
            raise UnauthorizedError(INVALID_TOKEN) from exc

        email = claims.get("email")
        # Without a verified e-mail there is no safe way to link invitations.
        if not isinstance(email, str) or claims.get("email_verified") is not True:
            raise UnauthorizedError("Google account email is not verified")

        name = claims.get("name")
        return GoogleIdentity(
            subject=str(claims["sub"]),
            email=email.lower(),
            name=name if isinstance(name, str) and name.strip() else None,
        )


@lru_cache
def get_google_verifier() -> GoogleTokenVerifier:
    return GoogleTokenVerifier.from_google(settings.auth.google_client_ids)
