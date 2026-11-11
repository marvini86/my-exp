import pytest
from pydantic import ValidationError

from schemas.account import CreateAccountRequest
from schemas.user import CreateUserRequest


class TestCreateAccountRequest:
    def test_normalizes_slug(self):
        request = CreateAccountRequest(name="  Família Silva ", slug="  Familia-Silva ")

        assert request.name == "Família Silva"
        assert request.slug == "familia-silva"

    @pytest.mark.parametrize(
        "slug",
        ["ab", "familia silva", "familia--silva", "-familia", "familia-", "família", "a" * 101],
    )
    def test_rejects_invalid_slug(self, slug):
        with pytest.raises(ValidationError):
            CreateAccountRequest(name="Conta", slug=slug)

    def test_ignores_active_from_client(self):
        request = CreateAccountRequest.model_validate(
            {"name": "Conta", "slug": "conta", "active": False}
        )

        assert not hasattr(request, "active")

    def test_rejects_blank_name(self):
        with pytest.raises(ValidationError):
            CreateAccountRequest(name="   ", slug="conta")


class TestCreateUserRequest:
    def test_valid_user(self):
        request = CreateUserRequest(name="Ana", email="ana@example.com")

        assert request.email == "ana@example.com"

    def test_rejects_invalid_email(self):
        with pytest.raises(ValidationError):
            CreateUserRequest(name="Ana", email="not-an-email")
