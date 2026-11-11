from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from core.auth import DEV_USER_HEADER
from models import Account, AccountMember, AccountRole

pytestmark = pytest.mark.integration


class TestUsersApi:
    def test_create_and_get_user(self, client, dev_environment):
        response = client.post("/users/", json={"name": "Ana", "email": "Ana@Example.com"})

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "ana@example.com"

        fetched = client.get(f"/users/{body['id']}", headers={DEV_USER_HEADER: body["id"]})
        assert fetched.status_code == 200
        assert fetched.json()["id"] == body["id"]

    def test_duplicate_email_is_conflict_case_insensitive(self, client, dev_environment):
        client.post("/users/", json={"name": "A", "email": "dup@example.com"})

        response = client.post("/users/", json={"name": "B", "email": "DUP@example.com"})

        assert response.status_code == 409
        assert response.json() == {"detail": "Email already registered"}

    def test_read_endpoints_require_authentication(self, client, dev_environment):
        assert client.get("/users/").status_code == 401
        assert client.get(f"/users/{uuid4()}").status_code == 401

    def test_unknown_user_is_not_found(self, client, auth_headers):
        assert client.get(f"/users/{uuid4()}", headers=auth_headers).status_code == 404

    def test_invalid_uuid_is_unprocessable(self, client, auth_headers):
        assert client.get("/users/123", headers=auth_headers).status_code == 422


class TestAccountsApi:
    def test_create_requires_authentication(self, client, dev_environment):
        response = client.post("/accounts/", json={"name": "Conta", "slug": "sem-auth"})

        assert response.status_code == 401

    def test_create_and_get_account(self, client, auth_headers):
        response = client.post(
            "/accounts/",
            json={"name": "Família Silva", "slug": "Familia-Silva"},
            headers=auth_headers,
        )

        assert response.status_code == 201
        body = response.json()
        assert body["slug"] == "familia-silva"
        assert body["active"] is True

        fetched = client.get(f"/accounts/{body['id']}", headers=auth_headers)
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Família Silva"

    def test_creator_becomes_owner(self, client, session, auth_headers):
        response = client.post(
            "/accounts/",
            json={"name": "Conta", "slug": "conta-owner"},
            headers=auth_headers,
        )
        account_id = UUID(response.json()["id"])
        creator_id = UUID(auth_headers[DEV_USER_HEADER])

        account = session.get(Account, account_id)
        members = session.scalars(
            select(AccountMember).where(AccountMember.account_id == account_id)
        ).all()

        assert account.created_by == creator_id
        assert account.updated_by == creator_id
        assert [(m.user_id, m.role) for m in members] == [(creator_id, AccountRole.OWNER)]

    def test_client_cannot_create_inactive_account(self, client, auth_headers):
        response = client.post(
            "/accounts/",
            json={"name": "Conta", "slug": "conta-x", "active": False},
            headers=auth_headers,
        )

        assert response.status_code == 201
        assert response.json()["active"] is True

    def test_duplicate_slug_is_conflict_and_creates_no_member(self, client, session, auth_headers):
        client.post("/accounts/", json={"name": "A", "slug": "mesmo-slug"}, headers=auth_headers)
        members_before = session.scalar(select(func.count()).select_from(AccountMember))

        response = client.post("/accounts/", json={"name": "B", "slug": "mesmo-slug"}, headers=auth_headers)

        assert response.status_code == 409
        assert response.json() == {"detail": "Slug already in use"}
        assert session.scalar(select(func.count()).select_from(AccountMember)) == members_before

    def test_unknown_account_is_not_found(self, client, auth_headers):
        assert client.get(f"/accounts/{uuid4()}", headers=auth_headers).status_code == 404
