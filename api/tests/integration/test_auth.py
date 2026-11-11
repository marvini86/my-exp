from uuid import uuid4

import pytest

from core.auth import DEV_USER_HEADER
from models.user import User

pytestmark = pytest.mark.integration


@pytest.fixture
def user_id(session) -> str:
    # Straight into the database: the sign-up route only exists in development.
    user = User(name="Ana", email="me@example.com")
    session.add(user)
    session.flush()
    return str(user.id)


@pytest.mark.usefixtures("dev_environment")
class TestDevAuthEnabled:
    def test_me_returns_current_user(self, client, user_id):
        response = client.get("/users/me", headers={DEV_USER_HEADER: user_id})

        assert response.status_code == 200
        assert response.json()["id"] == user_id
        assert response.json()["email"] == "me@example.com"

    def test_missing_header_is_unauthorized(self, client):
        response = client.get("/users/me")

        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}

    @pytest.mark.parametrize("value", ["not-a-uuid", "", "   "])
    def test_malformed_header_is_unauthorized(self, client, value):
        assert client.get("/users/me", headers={DEV_USER_HEADER: value}).status_code == 401

    def test_unknown_user_is_unauthorized(self, client):
        response = client.get("/users/me", headers={DEV_USER_HEADER: str(uuid4())})

        assert response.status_code == 401
        # Same message as an invalid UUID: does not reveal whether the user exists.
        assert response.json() == {"detail": "Not authenticated"}


@pytest.mark.usefixtures("production_environment")
class TestDevAuthDisabledOutsideDevelopment:
    def test_header_is_ignored_in_production(self, client, user_id):
        response = client.get("/users/me", headers={DEV_USER_HEADER: user_id})

        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}
