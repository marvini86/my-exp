"""Rate limiting (sliding window, in memory).

Protects login against brute force, and the routes that spend AI quota
(free models) or storage against abuse by a single client.

Per process: with N uvicorn workers the effective limit becomes N×. For a VPS
(1 process) that is enough; with several instances, move to something
shared (e.g. a PostgreSQL table).
Behind a reverse proxy, run uvicorn with --proxy-headers so the client's
real IP (X-Forwarded-For) is used.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Request

from core.auth import get_current_user
from core.config import settings
from core.exceptions import TooManyRequestsError
from models.user import User

# Above this, inactive keys are dropped (bounded memory).
_MAX_KEYS = 10_000


class SlidingWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: float) -> int | None:
        """Records the request. Blocked → seconds until allowed again; otherwise None."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, math.ceil(hits[0] + window_seconds - now))
            hits.append(now)
            if len(self._hits) > _MAX_KEYS:
                self._evict(now, window_seconds)
            return None

    def _evict(self, now: float, window_seconds: float) -> None:
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= now - window_seconds]
        for key in stale:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


@dataclass(frozen=True)
class Limit:
    scope: str
    requests: int
    window_seconds: int
    per: Literal["ip", "user"]


# Values tuned for family use: generous for humans, tight for scripts.
AUTH_LOGIN = Limit("auth_login", 10, 60, "ip")
AUTH_REFRESH = Limit("auth_refresh", 30, 60, "ip")
ASSISTANT_MESSAGE = Limit("assistant_message", 20, 60, "user")
SEMANTIC_SEARCH = Limit("semantic_search", 30, 60, "user")
DOCUMENT_UPLOAD = Limit("document_upload", 30, 60, "user")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check(limit: Limit, identity: str) -> None:
    if not settings.rate_limit_enabled:
        return
    retry_after = limiter.hit(f"{limit.scope}:{identity}", limit.requests, limit.window_seconds)
    if retry_after is not None:
        raise TooManyRequestsError("Too many requests, try again later", retry_after)


def rate_limit(limit: Limit) -> Callable[..., None]:
    """FastAPI dependency: `dependencies=[Depends(rate_limit(AUTH_LOGIN))]`."""
    if limit.per == "user":
        # get_current_user is cached per request: it does not authenticate twice.
        def by_user(user: User = Depends(get_current_user)) -> None:
            _check(limit, str(user.id))

        return by_user

    def by_ip(request: Request) -> None:
        _check(limit, _client_ip(request))

    return by_ip
