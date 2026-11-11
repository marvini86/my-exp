import pytest

pytestmark = pytest.mark.integration


def dev_login(client, email="dev@example.com", **extra):
    return client.post("/auth/dev-login", json={"email": email, **extra})


@pytest.mark.usefixtures("dev_environment")
class TestDevLogin:
    def test_issues_real_tokens_and_creates_user(self, client):
        response = dev_login(client, email="Dev@Example.com", name="Dev")

        assert response.status_code == 200, response.text
        tokens = response.json()
        assert tokens["access_token"].startswith("mxa_")
        assert (tokens["user"]["email"], tokens["user"]["name"]) == ("dev@example.com", "Dev")

        me = client.get("/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert me.json()["id"] == tokens["user"]["id"]

    def test_same_email_same_user(self, client):
        assert dev_login(client).json()["user"]["id"] == dev_login(client).json()["user"]["id"]

    def test_full_token_cycle(self, client):
        tokens = dev_login(client).json()

        refreshed = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
        headers = {"Authorization": f"Bearer {refreshed['access_token']}"}
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.get("/users/me", headers=headers).status_code == 401

    def test_works_with_users_created_by_dev_header_flow(self, client, make_user):
        user_id, headers = make_user("legacy@example.com")

        assert dev_login(client, email="legacy@example.com").json()["user"]["id"] == user_id
        assert client.get("/users/me", headers=headers).status_code == 200

    def test_invalid_email(self, client):
        assert dev_login(client, email="not-an-email").status_code == 422


@pytest.mark.usefixtures("production_environment")
def test_dev_login_does_not_exist_in_production(client):
    response = dev_login(client)

    assert response.status_code == 404
    assert "access_token" not in response.text
