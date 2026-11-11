from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI, Header
from fastapi.testclient import TestClient

from core.auth import get_current_user
from core.config import settings
from core.rate_limit import Limit, SlidingWindowLimiter, limiter, rate_limit
from core.exceptions import AppError
from main import app_error_handler


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_limit_then_blocks_with_retry_after():
    clock = Clock()
    rl = SlidingWindowLimiter(clock)

    assert [rl.hit("k", 3, 60) for _ in range(3)] == [None, None, None]
    clock.now += 20
    assert rl.hit("k", 3, 60) == 40  # the 1st hit leaves the window after 40 s


def test_window_slides():
    clock = Clock()
    rl = SlidingWindowLimiter(clock)
    for _ in range(2):
        rl.hit("k", 2, 10)

    clock.now += 10
    assert rl.hit("k", 2, 10) is None


def test_keys_are_independent():
    rl = SlidingWindowLimiter(Clock())
    rl.hit("a", 1, 60)

    assert rl.hit("a", 1, 60) is not None
    assert rl.hit("b", 1, 60) is None


def test_blocked_requests_do_not_extend_the_block():
    clock = Clock()
    rl = SlidingWindowLimiter(clock)
    rl.hit("k", 1, 60)
    for _ in range(5):
        rl.hit("k", 1, 60)

    clock.now += 60
    assert rl.hit("k", 1, 60) is None


@pytest.fixture
def tiny_app(monkeypatch):
    """Minimal app: tests the dependency without a database (the user comes from a header)."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    limiter.reset()
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    per_user, per_ip = Limit("t_user", 2, 60, "user"), Limit("t_ip", 2, 60, "ip")

    class FakeUser:
        def __init__(self, id_):
            self.id = id_

    users = {"ana": FakeUser(uuid4()), "bia": FakeUser(uuid4())}

    app.dependency_overrides[get_current_user] = lambda x_user=Header(): users[x_user]

    @app.get("/user", dependencies=[Depends(rate_limit(per_user))])
    def by_user():
        return {}

    @app.get("/ip", dependencies=[Depends(rate_limit(per_ip))])
    def by_ip():
        return {}

    yield TestClient(app)
    limiter.reset()


def test_user_limit_is_per_user(tiny_app):
    codes = [tiny_app.get("/user", headers={"x-user": "ana"}).status_code for _ in range(3)]

    assert codes == [200, 200, 429]
    assert tiny_app.get("/user", headers={"x-user": "bia"}).status_code == 200


def test_ip_limit_returns_retry_after(tiny_app):
    for _ in range(2):
        tiny_app.get("/ip")

    response = tiny_app.get("/ip")

    assert response.status_code == 429
    assert response.json() == {"detail": "Too many requests, try again later"}
    assert 1 <= int(response.headers["retry-after"]) <= 60


def test_can_be_disabled(tiny_app, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)

    assert {tiny_app.get("/ip").status_code for _ in range(5)} == {200}
