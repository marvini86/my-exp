"""Integration fixtures against a real PostgreSQL.

Requires TEST_DATABASE_URL pointing to a DISPOSABLE database with the
migrations applied, e.g.:
    postgresql+psycopg://postgres:postgres@localhost:55432/myexp_test

Each test runs inside an outer transaction rolled back at the end;
the services' `commit()` calls become SAVEPOINTs, so nothing persists.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


_DB_FIXTURES = {"engine", "session", "client"}


def pytest_collection_modifyitems(items):
    if TEST_DATABASE_URL:
        return

    skip = pytest.mark.skip(reason="TEST_DATABASE_URL não definida")
    for item in items:
        # Only skip what depends on PostgreSQL (e.g. Qdrant tests run without it).
        if "integration" in item.keywords and _DB_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    """The limiter is process-wide: every test starts from zero."""
    from core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def _open_platform_access(request, monkeypatch):
    """By default every e-mail is allowed on the platform (the tests are about something else).
    Tests marked `real_access` use the real rule (admin / allowed / invited)."""
    if "real_access" not in request.keywords:
        from repositories.platform_access import PlatformAccessRepository

        monkeypatch.setattr(PlatformAccessRepository, "is_listed", lambda self, email: True)


@pytest.fixture
def dev_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "environment", "development")


@pytest.fixture
def production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "environment", "production")


@pytest.fixture
def make_user(client: TestClient):
    """Creates users through the API and returns (id, dev authentication headers)."""
    from core.auth import DEV_USER_HEADER

    def _make_user(email: str, name: str = "Test User") -> tuple[str, dict[str, str]]:
        response = client.post("/users/", json={"name": name, "email": email})
        assert response.status_code == 201, response.text
        user_id = response.json()["id"]
        return user_id, {DEV_USER_HEADER: user_id}

    return _make_user


@pytest.fixture
def auth_headers(dev_environment, make_user) -> dict[str, str]:
    _, headers = make_user("owner@example.com", name="Owner")
    return headers


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    from database.connection import get_db
    from main import app

    app.dependency_overrides[get_db] = lambda: session
    try:
        # Without `with`: does not run the lifespan (which would connect to the .env database).
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
