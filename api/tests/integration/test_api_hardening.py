"""Hardening: health, headers, CORS, rate limiting and error format."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from main import add_cors

pytestmark = pytest.mark.integration


def test_health_checks_database(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_security_headers_on_every_response(client):
    for response in (client.get("/health"), client.get("/users/me")):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"


def test_error_format_is_uniform(client):
    assert client.get("/users/me").json() == {"detail": "Not authenticated"}
    assert client.get("/nao-existe").json() == {"detail": "Not Found"}


def test_login_is_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)

    codes = [client.post("/auth/refresh", json={"refresh_token": "mxr_x"}).status_code for _ in range(31)]

    assert codes[:30] == [401] * 30
    assert codes[30] == 429


class TestCors:
    @pytest.fixture
    def cors_client(self):
        app = FastAPI()
        add_cors(app, ["http://localhost:3000"])

        @app.get("/ping")
        def ping():
            return {}

        return TestClient(app)

    def test_allowed_origin_preflight(self, cors_client):
        response = cors_client.options(
            "/ping",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert "access-control-allow-credentials" not in response.headers

    def test_other_origin_is_not_allowed(self, cors_client):
        response = cors_client.get("/ping", headers={"Origin": "https://evil.example.com"})

        assert "access-control-allow-origin" not in response.headers

    def test_no_origins_means_no_cors(self):
        app = FastAPI()
        add_cors(app, [])

        assert app.user_middleware == []


@pytest.mark.parametrize(("environment", "override", "expected"), [
    ("development", None, True), ("production", None, False), ("production", True, True), ("development", False, False),
])
def test_docs_visibility(monkeypatch, environment, override, expected):
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "docs_enabled", override)

    assert settings.docs_visible is expected


def test_oversized_json_body_is_rejected_before_parsing(client):
    response = client.post("/auth/refresh", content=b"{" + b" " * 70_000 + b"}", headers={"Content-Type": "application/json"})

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    # Security headers also apply to middleware responses.
    assert response.headers["cache-control"] == "no-store"
