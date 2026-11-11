"""Google-shaped ID tokens, signed with a local RSA key (tests)."""

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from services.google_identity import GoogleTokenVerifier

CLIENT_ID = "test-client.apps.googleusercontent.com"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_id_token(
    sub: str = "google-sub-1",
    email: str = "ana@gmail.com",
    *,
    name: str | None = "Ana Souza",
    email_verified: bool = True,
    aud: str = CLIENT_ID,
    iss: str = "https://accounts.google.com",
    expires_in: int = 3600,
    signed_by_other_key: bool = False,
    **extra: Any,
) -> str:
    now = int(time.time())
    claims = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "iat": now,
        "exp": now + expires_in,
        **extra,
    }
    if name is not None:
        claims["name"] = name
    key = _OTHER_KEY if signed_by_other_key else _KEY
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})


def make_verifier(client_ids: list[str] | None = None) -> GoogleTokenVerifier:
    public_key = _KEY.public_key()
    return GoogleTokenVerifier([CLIENT_ID] if client_ids is None else client_ids, lambda _token: public_key)
