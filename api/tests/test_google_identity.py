import jwt
import pytest

from core.exceptions import ServiceUnavailableError, UnauthorizedError
from google_tokens import CLIENT_ID, make_id_token, make_verifier


def test_valid_token_returns_identity():
    identity = make_verifier().verify(make_id_token(email="Ana@Gmail.com"))

    assert (identity.subject, identity.email, identity.name) == ("google-sub-1", "ana@gmail.com", "Ana Souza")


def test_legacy_issuer_without_https_is_accepted():
    assert make_verifier().verify(make_id_token(iss="accounts.google.com")).subject == "google-sub-1"


def test_any_configured_client_id_is_accepted():
    verifier = make_verifier(["web-client", CLIENT_ID])

    assert verifier.verify(make_id_token()).email == "ana@gmail.com"


def test_blank_name_becomes_none():
    assert make_verifier().verify(make_id_token(name="  ")).name is None


@pytest.mark.parametrize(
    "token_kwargs",
    [
        {"aud": "someone-elses-client"},
        {"iss": "https://evil.example.com"},
        {"expires_in": -3600},
        {"signed_by_other_key": True},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired", "bad-signature"],
)
def test_invalid_tokens_are_rejected(token_kwargs):
    with pytest.raises(UnauthorizedError, match="Invalid Google token"):
        make_verifier().verify(make_id_token(**token_kwargs))


def test_unsigned_token_is_rejected():
    token = jwt.encode({"sub": "x", "aud": CLIENT_ID, "iss": "accounts.google.com"}, key=None, algorithm="none")

    with pytest.raises(UnauthorizedError):
        make_verifier().verify(token)


def test_hmac_token_is_rejected():
    # Classic attack: HS256 "signed" with an arbitrary secret.
    token = jwt.encode({"sub": "x", "aud": CLIENT_ID, "iss": "accounts.google.com"}, "secret", algorithm="HS256")

    with pytest.raises(UnauthorizedError):
        make_verifier().verify(token)


def test_garbage_is_rejected():
    with pytest.raises(UnauthorizedError):
        make_verifier().verify("not-a-jwt")


@pytest.mark.parametrize("token_kwargs", [{"email_verified": False}, {"email": None}], ids=["unverified", "no-email"])
def test_unverified_email_is_rejected(token_kwargs):
    with pytest.raises(UnauthorizedError, match="not verified"):
        make_verifier().verify(make_id_token(**token_kwargs))


def test_without_client_ids_login_is_disabled():
    with pytest.raises(ServiceUnavailableError):
        make_verifier([]).verify(make_id_token())
