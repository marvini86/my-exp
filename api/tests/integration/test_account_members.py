from dataclasses import dataclass
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("dev_environment")]


@dataclass
class Person:
    id: str
    email: str
    headers: dict[str, str]


@dataclass
class Family:
    account_id: str
    owner: Person
    admin: Person
    member: Person
    outsider: Person

    def members_url(self, user: Person | None = None) -> str:
        base = f"/accounts/{self.account_id}/members/"
        return base if user is None else f"{base}{user.id}"


@pytest.fixture
def family(client, make_user) -> Family:
    """Account with owner, admin and member; outsider does not belong to the account."""

    def person(label: str) -> Person:
        email = f"{label}@example.com"
        user_id, headers = make_user(email, name=label.title())
        return Person(user_id, email, headers)

    owner, admin, member, outsider = (person(p) for p in ("owner", "admin", "member", "outsider"))

    response = client.post("/accounts/", json={"name": "Família", "slug": "familia"}, headers=owner.headers)
    assert response.status_code == 201
    account_id = response.json()["id"]

    url = f"/accounts/{account_id}/members/"
    for who, role in ((admin, "admin"), (member, "member")):
        added = client.post(url, json={"email": who.email, "role": role}, headers=owner.headers)
        assert added.status_code == 201, added.text

    return Family(account_id, owner, admin, member, outsider)


def roles(client, family: Family) -> dict[str, str]:
    response = client.get(family.members_url(), headers=family.owner.headers)
    return {m["email"]: m["role"] for m in response.json()}


class TestVisibility:
    def test_my_accounts_lists_role(self, client, family):
        response = client.get("/accounts/me", headers=family.admin.headers)

        assert response.status_code == 200
        assert [(a["id"], a["role"]) for a in response.json()] == [(family.account_id, "admin")]

    def test_outsider_has_no_accounts(self, client, family):
        assert client.get("/accounts/me", headers=family.outsider.headers).json() == []

    def test_any_member_can_read_account_and_members(self, client, family):
        for person in (family.owner, family.admin, family.member):
            assert client.get(f"/accounts/{family.account_id}", headers=person.headers).status_code == 200
            members = client.get(family.members_url(), headers=person.headers)
            assert members.status_code == 200
            assert len(members.json()) == 3

    def test_outsider_gets_404_not_403(self, client, family):
        account = client.get(f"/accounts/{family.account_id}", headers=family.outsider.headers)
        members = client.get(family.members_url(), headers=family.outsider.headers)

        assert account.status_code == 404
        assert members.status_code == 404
        assert account.json() == {"detail": "Account not found"}

    def test_users_visible_only_through_shared_accounts(self, client, family):
        visible_to_member = {u["email"] for u in client.get("/users/", headers=family.member.headers).json()}
        visible_to_outsider = {u["email"] for u in client.get("/users/", headers=family.outsider.headers).json()}

        assert visible_to_member == {"owner@example.com", "admin@example.com", "member@example.com"}
        assert visible_to_outsider == {"outsider@example.com"}
        assert client.get(f"/users/{family.owner.id}", headers=family.outsider.headers).status_code == 404
        assert client.get(f"/users/{family.owner.id}", headers=family.member.headers).status_code == 200


class TestAddMember:
    def test_owner_adds_member_by_email_case_insensitive(self, client, family, make_user):
        make_user("new@example.com")

        response = client.post(family.members_url(), json={"email": "NEW@example.com"}, headers=family.owner.headers)

        assert response.status_code == 201
        assert response.json()["role"] == "member"
        assert response.json()["email"] == "new@example.com"

    def test_admin_adds_member(self, client, family, make_user):
        make_user("new@example.com")

        response = client.post(family.members_url(), json={"email": "new@example.com"}, headers=family.admin.headers)

        assert response.status_code == 201

    @pytest.mark.parametrize("role", ["admin", "owner"])
    def test_admin_cannot_grant_elevated_roles(self, client, family, make_user, role):
        make_user("new@example.com")

        response = client.post(
            family.members_url(),
            json={"email": "new@example.com", "role": role},
            headers=family.admin.headers,
        )

        assert response.status_code == 403

    def test_member_cannot_add(self, client, family, make_user):
        make_user("new@example.com")

        response = client.post(family.members_url(), json={"email": "new@example.com"}, headers=family.member.headers)

        assert response.status_code == 403

    def test_outsider_cannot_add(self, client, family):
        response = client.post(
            family.members_url(),
            json={"email": family.outsider.email},
            headers=family.outsider.headers,
        )

        assert response.status_code == 404

    def test_unknown_email_becomes_pending_invite(self, client, family):
        response = client.post(family.members_url(), json={"email": "Ghost@Example.com"}, headers=family.owner.headers)

        assert response.status_code == 201
        body = response.json()
        assert (body["email"], body["name"], body["role"], body["pending"]) == ("ghost@example.com", None, "member", True)

        again = client.post(family.members_url(), json={"email": "ghost@example.com"}, headers=family.owner.headers)
        assert again.status_code == 409

    def test_existing_member_is_conflict(self, client, family):
        response = client.post(family.members_url(), json={"email": family.member.email}, headers=family.owner.headers)

        assert response.status_code == 409

    def test_invalid_role_is_unprocessable(self, client, family):
        response = client.post(
            family.members_url(),
            json={"email": family.outsider.email, "role": "superadmin"},
            headers=family.owner.headers,
        )

        assert response.status_code == 422


class TestUpdateRole:
    def test_owner_promotes_member_to_admin(self, client, family):
        response = client.patch(family.members_url(family.member), json={"role": "admin"}, headers=family.owner.headers)

        assert response.status_code == 200
        assert response.json()["role"] == "admin"

    def test_admin_cannot_promote_member(self, client, family):
        response = client.patch(family.members_url(family.member), json={"role": "admin"}, headers=family.admin.headers)

        assert response.status_code == 403
        assert roles(client, family)["member@example.com"] == "member"

    def test_admin_cannot_change_owner(self, client, family):
        response = client.patch(family.members_url(family.owner), json={"role": "member"}, headers=family.admin.headers)

        assert response.status_code == 403

    def test_member_cannot_change_roles(self, client, family):
        response = client.patch(family.members_url(family.member), json={"role": "owner"}, headers=family.member.headers)

        assert response.status_code == 403

    def test_last_owner_cannot_be_demoted(self, client, family):
        response = client.patch(family.members_url(family.owner), json={"role": "admin"}, headers=family.owner.headers)

        assert response.status_code == 409
        assert response.json() == {"detail": "Account must have at least one owner"}

    def test_owner_can_step_down_when_another_owner_exists(self, client, family):
        client.patch(family.members_url(family.admin), json={"role": "owner"}, headers=family.owner.headers)

        response = client.patch(family.members_url(family.owner), json={"role": "member"}, headers=family.owner.headers)

        assert response.status_code == 200
        # The former owner stays in the account (as member) and can still list members.
        assert roles(client, family) == {
            "owner@example.com": "member",
            "admin@example.com": "owner",
            "member@example.com": "member",
        }

    def test_unknown_member_is_not_found(self, client, family):
        response = client.patch(f"{family.members_url()}{uuid4()}", json={"role": "admin"}, headers=family.owner.headers)

        assert response.status_code == 404
        assert response.json() == {"detail": "Member not found"}


class TestRemoveMember:
    def test_owner_removes_admin(self, client, family):
        response = client.delete(family.members_url(family.admin), headers=family.owner.headers)

        assert response.status_code == 204
        assert "admin@example.com" not in roles(client, family)
        # A former member loses access immediately.
        assert client.get(f"/accounts/{family.account_id}", headers=family.admin.headers).status_code == 404

    def test_admin_removes_member(self, client, family):
        assert client.delete(family.members_url(family.member), headers=family.admin.headers).status_code == 204

    def test_admin_cannot_remove_owner(self, client, family):
        assert client.delete(family.members_url(family.owner), headers=family.admin.headers).status_code == 403

    def test_member_cannot_remove_others(self, client, family):
        assert client.delete(family.members_url(family.admin), headers=family.member.headers).status_code == 403

    def test_member_can_leave(self, client, family):
        response = client.delete(family.members_url(family.member), headers=family.member.headers)

        assert response.status_code == 204
        assert client.get("/accounts/me", headers=family.member.headers).json() == []

    def test_last_owner_cannot_leave(self, client, family):
        response = client.delete(family.members_url(family.owner), headers=family.owner.headers)

        assert response.status_code == 409

    def test_outsider_cannot_remove(self, client, family):
        assert client.delete(family.members_url(family.member), headers=family.outsider.headers).status_code == 404

    def test_unknown_account_is_not_found(self, client, family):
        response = client.delete(f"/accounts/{uuid4()}/members/{family.member.id}", headers=family.owner.headers)

        assert response.status_code == 404
