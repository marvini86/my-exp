from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from database.tenant_context import set_account_context
from models import Category

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("dev_environment")]


@pytest.fixture
def two_accounts(client, make_user):
    """Two independent accounts, each with its owner; A also has a member."""
    owner_a_id, owner_a = make_user("owner-a@example.com")
    owner_b_id, owner_b = make_user("owner-b@example.com")
    member_a_id, member_a = make_user("member-a@example.com")

    account_a = client.post("/accounts/", json={"name": "A", "slug": "conta-a"}, headers=owner_a).json()["id"]
    account_b = client.post("/accounts/", json={"name": "B", "slug": "conta-b"}, headers=owner_b).json()["id"]
    client.post(f"/accounts/{account_a}/members/", json={"email": "member-a@example.com"}, headers=owner_a)

    return {
        "account_a": account_a,
        "account_b": account_b,
        "owner_a": owner_a,
        "owner_b": owner_b,
        "member_a": member_a,
        "owner_a_id": owner_a_id,
        "owner_b_id": owner_b_id,
    }


def url(account_id: str) -> str:
    return f"/accounts/{account_id}/categories/"


class TestCategoriesApi:
    def test_owner_creates_and_lists(self, client, two_accounts):
        a = two_accounts["account_a"]

        created = client.post(
            url(a),
            json={"name": "  Mercado ", "description": "Supermercados"},
            headers=two_accounts["owner_a"],
        )

        assert created.status_code == 201
        assert created.json()["name"] == "Mercado"

        listed = client.get(url(a), headers=two_accounts["owner_a"])
        assert [c["name"] for c in listed.json()] == ["Mercado"]

    def test_member_can_list_but_not_create(self, client, two_accounts):
        a = two_accounts["account_a"]
        client.post(url(a), json={"name": "Farmácia"}, headers=two_accounts["owner_a"])

        assert [c["name"] for c in client.get(url(a), headers=two_accounts["member_a"]).json()] == ["Farmácia"]
        assert client.post(url(a), json={"name": "Lazer"}, headers=two_accounts["member_a"]).status_code == 403

    def test_duplicate_name_is_conflict(self, client, two_accounts):
        a = two_accounts["account_a"]
        client.post(url(a), json={"name": "Energia"}, headers=two_accounts["owner_a"])

        response = client.post(url(a), json={"name": "Energia"}, headers=two_accounts["owner_a"])

        assert response.status_code == 409

    def test_same_name_allowed_in_different_accounts(self, client, two_accounts):
        ok_a = client.post(url(two_accounts["account_a"]), json={"name": "Água"}, headers=two_accounts["owner_a"])
        ok_b = client.post(url(two_accounts["account_b"]), json={"name": "Água"}, headers=two_accounts["owner_b"])

        assert (ok_a.status_code, ok_b.status_code) == (201, 201)

    def test_accounts_are_isolated(self, client, two_accounts):
        client.post(url(two_accounts["account_a"]), json={"name": "Só da A"}, headers=two_accounts["owner_a"])
        client.post(url(two_accounts["account_b"]), json={"name": "Só da B"}, headers=two_accounts["owner_b"])

        seen_by_b = client.get(url(two_accounts["account_b"]), headers=two_accounts["owner_b"]).json()

        assert [c["name"] for c in seen_by_b] == ["Só da B"]

    def test_non_member_gets_404(self, client, two_accounts):
        a = two_accounts["account_a"]

        assert client.get(url(a), headers=two_accounts["owner_b"]).status_code == 404
        assert client.post(url(a), json={"name": "Invasão"}, headers=two_accounts["owner_b"]).status_code == 404

    def test_unknown_account_is_404(self, client, two_accounts):
        assert client.get(url(str(uuid4())), headers=two_accounts["owner_a"]).status_code == 404

    def test_requires_authentication(self, client, two_accounts):
        assert client.get(url(two_accounts["account_a"])).status_code == 401

    @pytest.mark.parametrize("payload", [{"name": ""}, {"name": "   "}, {"name": "x" * 151}, {}])
    def test_invalid_payload_is_unprocessable(self, client, two_accounts, payload):
        response = client.post(url(two_accounts["account_a"]), json=payload, headers=two_accounts["owner_a"])

        assert response.status_code == 422


class TestRowLevelSecurity:
    """The database isolates the accounts even without an account_id filter in the query."""

    @pytest.fixture(autouse=True)
    def require_rls_enforced(self, session):
        bypasses = session.execute(
            text("select rolsuper or rolbypassrls from pg_roles where rolname = current_user")
        ).scalar_one()
        if bypasses:
            pytest.skip("o role de teste ignora RLS; rode com myexp_app")

    def test_unfiltered_query_only_sees_active_account(self, client, session, two_accounts):
        client.post(url(two_accounts["account_a"]), json={"name": "Da A"}, headers=two_accounts["owner_a"])
        client.post(url(two_accounts["account_b"]), json={"name": "Da B"}, headers=two_accounts["owner_b"])

        set_account_context(session, UUID(two_accounts["account_b"]), UUID(two_accounts["owner_b_id"]))
        names = session.scalars(select(Category.name)).all()  # no WHERE account_id

        assert names == ["Da B"]

    def test_cannot_write_into_another_account(self, client, session, two_accounts):
        from sqlalchemy.exc import ProgrammingError

        set_account_context(session, UUID(two_accounts["account_b"]), UUID(two_accounts["owner_b_id"]))
        session.add(Category(account_id=UUID(two_accounts["account_a"]), name="Invasão"))

        with pytest.raises(ProgrammingError, match="row-level security"):
            session.flush()

    def test_context_requires_membership(self, session, two_accounts):
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError, match="not a member"):
            set_account_context(session, UUID(two_accounts["account_a"]), UUID(two_accounts["owner_b_id"]))
