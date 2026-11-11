"""Google login, sessions and tokens.

Google is simulated with ID tokens signed by a local RSA key: the
real validation (signature, aud, iss, exp) runs, only the key source changes.
Defaults to ENVIRONMENT=production: the Bearer token must work without the
development header.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError

from core.auth import DEV_USER_HEADER
from core.config import settings
from google_tokens import make_id_token, make_verifier
from models.user import User
from models.user_session import UserSession
from services.auth import hash_token
from services.google_identity import get_google_verifier

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def google(client, production_environment):
    from main import app

    app.dependency_overrides[get_google_verifier] = lambda: make_verifier()
    yield
    app.dependency_overrides.pop(get_google_verifier, None)


def login(client, **token_kwargs):
    return client.post("/auth/google", json={"id_token": make_id_token(**token_kwargs)}, headers={"User-Agent": "pytest"})


def bearer(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def stored_session(session, tokens: dict) -> UserSession:
    return session.scalars(
        select(UserSession).where(UserSession.access_token_hash == hash_token(tokens["access_token"]))
    ).one()


class TestLogin:
    def test_first_login_creates_user_and_session(self, client, session):
        response = login(client)

        assert response.status_code == 200, response.text
        tokens = response.json()
        assert tokens["token_type"] == "bearer"
        assert tokens["access_token"].startswith("mxa_") and tokens["refresh_token"].startswith("mxr_")
        assert (tokens["expires_in"], tokens["refresh_expires_in"]) == (900, 30 * 86400)
        assert tokens["user"]["email"] == "ana@gmail.com"

        me = client.get("/users/me", headers=bearer(tokens))
        assert me.status_code == 200
        assert me.json()["id"] == tokens["user"]["id"]

        user = session.get(User, me.json()["id"])
        assert (user.identity_provider, user.external_subject, user.name) == ("google", "google-sub-1", "Ana Souza")

    def test_tokens_are_stored_only_as_hashes(self, client, session):
        tokens = login(client).json()

        stored = stored_session(session, tokens)
        assert stored.refresh_token_hash == hash_token(tokens["refresh_token"])
        assert tokens["access_token"] not in (stored.access_token_hash, stored.refresh_token_hash)
        assert stored.user_agent == "pytest"

    def test_next_login_reuses_user_and_syncs_profile(self, client):
        first = login(client).json()
        second = login(client, name="Ana S. Lima", email="ANA.LIMA@gmail.com").json()

        assert second["user"]["id"] == first["user"]["id"]
        assert (second["user"]["name"], second["user"]["email"]) == ("Ana S. Lima", "ana.lima@gmail.com")
        # Independent sessions: both remain valid.
        assert client.get("/users/me", headers=bearer(first)).status_code == 200
        assert client.get("/users/me", headers=bearer(second)).status_code == 200

    def test_invite_is_linked_on_first_login(self, client):
        owner = login(client, sub="owner-sub", email="owner@gmail.com").json()
        account = client.post("/accounts/", json={"name": "Família", "slug": "familia-login"}, headers=bearer(owner)).json()
        members_url = f"/accounts/{account['id']}/members/"

        invite = client.post(members_url, json={"email": "Bia@Gmail.com"}, headers=bearer(owner))
        assert invite.status_code == 201
        assert invite.json()["pending"] is True

        bia = login(client, sub="bia-sub", email="bia@gmail.com", name="Bia").json()

        assert bia["user"]["id"] == invite.json()["user_id"]
        assert [a["id"] for a in client.get("/accounts/me", headers=bearer(bia)).json()] == [account["id"]]
        members = {m["email"]: m for m in client.get(members_url, headers=bearer(owner)).json()}
        assert (members["bia@gmail.com"]["pending"], members["bia@gmail.com"]["name"]) == (False, "Bia")

    def test_email_linked_to_another_google_account_is_conflict(self, client):
        login(client, sub="sub-a", email="shared@gmail.com")

        response = login(client, sub="sub-b", email="shared@gmail.com")

        assert response.status_code == 409

    @pytest.mark.parametrize(
        "token_kwargs",
        [{"aud": "other-client"}, {"expires_in": -3600}, {"signed_by_other_key": True}, {"email_verified": False}],
        ids=["audience", "expired", "signature", "unverified"],
    )
    def test_invalid_google_token_is_unauthorized(self, client, token_kwargs):
        response = login(client, **token_kwargs)

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert "access_token" not in response.text

    def test_login_disabled_without_client_id(self, client):
        from main import app

        app.dependency_overrides[get_google_verifier] = lambda: make_verifier([])

        assert login(client).status_code == 503


class TestRefresh:
    def test_refresh_rotates_both_tokens(self, client):
        first = login(client).json()

        response = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})

        assert response.status_code == 200
        second = response.json()
        assert second["access_token"] != first["access_token"]
        assert second["refresh_token"] != first["refresh_token"]
        assert client.get("/users/me", headers=bearer(second)).status_code == 200
        assert client.get("/users/me", headers=bearer(first)).status_code == 401

    def test_parallel_refresh_within_grace_is_conflict_not_revocation(self, client):
        first = login(client).json()
        second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

        # Another tab/request used the same refresh token right after: 409, session intact.
        retry = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
        assert retry.status_code == 409
        assert retry.json() == {"detail": "Refresh token already rotated"}
        assert client.get("/users/me", headers=bearer(second)).status_code == 200
        assert client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 200

    def test_reused_refresh_token_after_grace_revokes_the_session(self, client, session):
        first = login(client).json()
        second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()
        stored_session(session, second).previous_refresh_grace_until = datetime.now(UTC) - timedelta(seconds=1)
        session.flush()

        # The old copy shows up after the window (e.g. stolen): the whole session is revoked.
        assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
        assert client.get("/users/me", headers=bearer(second)).status_code == 401
        assert client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401

    def test_expired_refresh_token(self, client, session):
        tokens = login(client).json()
        stored_session(session, tokens).refresh_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.flush()

        assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401

    @pytest.mark.parametrize("token", ["mxr_unknown", "x" * 256])
    def test_unknown_refresh_token(self, client, token):
        assert client.post("/auth/refresh", json={"refresh_token": token}).status_code == 401


class TestAccessToken:
    def test_expired_access_token_is_unauthorized(self, client, session):
        tokens = login(client).json()
        stored_session(session, tokens).access_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.flush()

        assert client.get("/users/me", headers=bearer(tokens)).status_code == 401
        # The refresh token is still valid and restores access.
        refreshed = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
        assert client.get("/users/me", headers=bearer(refreshed)).status_code == 200

    @pytest.mark.parametrize("header", ["Bearer mxa_invalid", "Bearer ", "Basic dXNlcjpwdw=="])
    def test_invalid_authorization_header(self, client, header):
        response = client.get("/users/me", headers={"Authorization": header})

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_invalid_bearer_never_falls_back_to_dev_header(self, client, monkeypatch):
        tokens = login(client).json()
        monkeypatch.setattr(settings, "environment", "development")

        response = client.get(
            "/users/me", headers={"Authorization": "Bearer mxa_invalid", DEV_USER_HEADER: tokens["user"]["id"]}
        )

        assert response.status_code == 401

    def test_dev_header_ignored_in_production(self, client):
        tokens = login(client).json()

        assert client.get("/users/me", headers={DEV_USER_HEADER: tokens["user"]["id"]}).status_code == 401

    def test_dev_signup_route_hidden_in_production(self, client):
        assert client.post("/users/", json={"name": "X", "email": "x@example.com"}).status_code == 404


class TestLogout:
    def test_logout_revokes_current_session_only(self, client):
        phone = login(client).json()
        laptop = login(client).json()

        assert client.post("/auth/logout", headers=bearer(phone)).status_code == 204

        assert client.get("/users/me", headers=bearer(phone)).status_code == 401
        assert client.post("/auth/refresh", json={"refresh_token": phone["refresh_token"]}).status_code == 401
        assert client.get("/users/me", headers=bearer(laptop)).status_code == 200

    def test_logout_everywhere(self, client):
        phone = login(client).json()
        laptop = login(client).json()

        assert client.post("/auth/logout?everywhere=true", headers=bearer(phone)).status_code == 204

        assert client.get("/users/me", headers=bearer(laptop)).status_code == 401
        assert client.post("/auth/refresh", json={"refresh_token": laptop["refresh_token"]}).status_code == 401

    def test_logout_requires_bearer(self, client):
        assert client.post("/auth/logout").status_code == 401

    def test_old_sessions_are_cleaned_on_login(self, client, session):
        old = login(client).json()
        client.post("/auth/logout", headers=bearer(old))

        login(client)

        hashes = session.scalars(select(UserSession.access_token_hash)).all()
        assert hash_token(old["access_token"]) not in hashes


WORKER_URL = os.getenv("TEST_WORKER_DATABASE_URL")


@pytest.mark.skipif(not WORKER_URL, reason="TEST_WORKER_DATABASE_URL não definida")
def test_worker_role_cannot_read_sessions():
    engine = create_engine(WORKER_URL)
    try:
        with engine.connect() as connection, pytest.raises(ProgrammingError, match="permission denied"):
            connection.execute(text("SELECT 1 FROM user_sessions LIMIT 1"))
    finally:
        engine.dispose()


@pytest.mark.real_access
class TestAccessControl:
    """Closed platform: admin (AUTH_ADMIN_EMAILS), e-mails they allowed and people invited to accounts."""

    ADMIN = "admin@gmail.com"

    @pytest.fixture(autouse=True)
    def admin(self, monkeypatch):
        monkeypatch.setattr(settings.auth, "admin_emails", [self.ADMIN])

    def admin_login(self, client):
        return login(client, sub="admin-sub", email=self.ADMIN, name="Admin").json()

    def test_unknown_email_is_blocked_and_not_created(self, client, session):
        response = login(client, sub="x", email="intruso@gmail.com")

        assert response.status_code == 403
        assert session.scalars(select(User).where(User.email == "intruso@gmail.com")).first() is None

    def test_admin_enters_and_is_flagged(self, client):
        admin = self.admin_login(client)

        assert client.get("/users/me", headers=bearer(admin)).json()["is_platform_admin"] is True

    def test_admin_grants_access(self, client):
        admin = self.admin_login(client)

        granted = client.post("/admin/access", json={"email": "Novo@Gmail.com"}, headers=bearer(admin))
        assert granted.status_code == 201
        assert client.post("/admin/access", json={"email": "novo@gmail.com"}, headers=bearer(admin)).status_code == 409

        novo = login(client, sub="novo-sub", email="novo@gmail.com")
        assert novo.status_code == 200
        assert client.get("/users/me", headers=bearer(novo.json())).json()["is_platform_admin"] is False

        listed = client.get("/admin/access", headers=bearer(admin)).json()
        assert [(e["email"], e["registered"]) for e in listed] == [("novo@gmail.com", True)]

    def test_account_invite_grants_access(self, client):
        admin = self.admin_login(client)
        account = client.post("/accounts/", json={"name": "Família", "slug": "familia-acesso"}, headers=bearer(admin)).json()
        client.post(f"/accounts/{account['id']}/members/", json={"email": "convidada@gmail.com"}, headers=bearer(admin))

        # Not on the admin's list, but was invited to an account.
        assert login(client, sub="conv-sub", email="convidada@gmail.com").status_code == 200

    def test_member_can_invite_anyone(self, client):
        admin = self.admin_login(client)
        client.post("/admin/access", json={"email": "dono@gmail.com"}, headers=bearer(admin))
        dono = login(client, sub="dono-sub", email="dono@gmail.com").json()
        account = client.post("/accounts/", json={"name": "Casa", "slug": "casa-dono"}, headers=bearer(dono)).json()
        client.post(f"/accounts/{account['id']}/members/", json={"email": "amigo@gmail.com"}, headers=bearer(dono))

        assert login(client, sub="amigo-sub", email="amigo@gmail.com").status_code == 200

    def test_invited_user_cannot_invite(self, client):
        admin = self.admin_login(client)
        client.post("/admin/access", json={"email": "dono2@gmail.com"}, headers=bearer(admin))
        dono = login(client, sub="dono2-sub", email="dono2@gmail.com").json()
        account = client.post("/accounts/", json={"name": "Casa", "slug": "casa-dono2"}, headers=bearer(dono)).json()
        members = f"/accounts/{account['id']}/members/"
        client.post(members, json={"email": "filha@gmail.com", "role": "admin"}, headers=bearer(dono))
        filha = login(client, sub="filha-sub", email="filha@gmail.com").json()

        me = client.get("/users/me", headers=bearer(filha)).json()
        assert (me["can_invite"], me["is_platform_admin"]) == (False, False)
        # Even as account admin, whoever came in by invitation cannot invite.
        response = client.post(members, json={"email": "namorado@gmail.com"}, headers=bearer(filha))
        assert response.status_code == 403
        assert response.json() == {"detail": "Only people granted by the platform admin can invite"}
        # Not even in an account they created.
        own = client.post("/accounts/", json={"name": "Minha", "slug": "conta-filha"}, headers=bearer(filha)).json()
        assert client.post(f"/accounts/{own['id']}/members/", json={"email": "z@gmail.com"}, headers=bearer(filha)).status_code == 403
        assert login(client, sub="nam-sub", email="namorado@gmail.com").status_code == 403

        assert client.get("/users/me", headers=bearer(dono)).json()["can_invite"] is True

    def test_revoked_access_drops_session_on_refresh(self, client):
        admin = self.admin_login(client)
        entry = client.post("/admin/access", json={"email": "temp@gmail.com"}, headers=bearer(admin)).json()
        temp = login(client, sub="temp-sub", email="temp@gmail.com").json()

        assert client.delete(f"/admin/access/{entry['id']}", headers=bearer(admin)).status_code == 204

        assert client.post("/auth/refresh", json={"refresh_token": temp["refresh_token"]}).status_code == 401
        assert login(client, sub="temp-sub", email="temp@gmail.com").status_code == 403

    def test_admin_routes_hidden_from_others(self, client):
        admin = self.admin_login(client)
        entry = client.post("/admin/access", json={"email": "comum@gmail.com"}, headers=bearer(admin)).json()
        comum = login(client, sub="comum-sub", email="comum@gmail.com").json()

        assert client.get("/admin/access", headers=bearer(comum)).status_code == 404
        assert client.post("/admin/access", json={"email": "x@gmail.com"}, headers=bearer(comum)).status_code == 404
        assert client.delete(f"/admin/access/{entry['id']}", headers=bearer(comum)).status_code == 404
        assert client.get("/admin/access").status_code == 401
