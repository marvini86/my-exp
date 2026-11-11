"""OpenRouter (free) model catalog with fallback and "cooldown".

- `AGENT_ALLOWED_MODELS` set → only those models, in list order.
- Empty → every free model from the public API (`/api/v1/models`),
  with `openrouter/free` (automatic router) last.
- The API also reports each model's capabilities (vision, structured
  output); the list is refreshed periodically because it changes often.
- A model that hits a limit or becomes unavailable enters a "cooldown" and is
  skipped until it expires.
"""

import logging
import ssl
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

MODELS_URL = "https://openrouter.ai/api/v1/models"
FREE_ROUTER = "openrouter/free"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    vision: bool
    structured_output: bool
    provider: str = "openrouter"
    # No native structured output, but accepts response_format json_object (OpenAI-compatible APIs).
    json_mode: bool = False


class CooldownTracker:
    """Pauses per model and per provider (account-wide quota, e.g. OpenRouter's daily limit).

    `daily` marks pauses caused by a daily limit: the UI says "back at X" instead of "in a moment".
    """

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._models: dict[str, tuple[float, bool]] = {}
        self._providers: dict[str, tuple[float, bool]] = {}
        self._cooldown_lock = threading.Lock()

    def cool_down(self, model: str, seconds: float, *, daily: bool = False) -> None:
        with self._cooldown_lock:
            self._models[model] = (self._clock() + seconds, daily)
        logger.info("Model on cooldown | model=%s seconds=%s daily=%s", model, int(seconds), daily)

    def cool_down_provider(self, provider: str, seconds: float, *, daily: bool = False) -> None:
        with self._cooldown_lock:
            self._providers[provider] = (self._clock() + seconds, daily)
        logger.info("Provider on cooldown | provider=%s seconds=%s daily=%s", provider, int(seconds), daily)

    def _blocked_until(self, info: ModelInfo) -> tuple[float, bool]:
        with self._cooldown_lock:
            return max(self._models.get(info.id, (0.0, False)), self._providers.get(info.provider, (0.0, False)))

    def is_available(self, info: ModelInfo) -> bool:
        return self._blocked_until(info)[0] <= self._clock()

    def wait_info(self, models: Sequence[ModelInfo]) -> tuple[float, bool] | None:
        """(seconds until the first of these models is back, whether the wait is due to a daily limit)."""
        now = self._clock()
        waits = [(until - now, daily) for until, daily in map(self._blocked_until, models) if until > now]
        return min(waits) if waits else None

    def seconds_until_available(self) -> float | None:
        """How long until the first pause (model or provider) ends."""
        now = self._clock()
        with self._cooldown_lock:
            ends = [until - now for until, _ in [*self._models.values(), *self._providers.values()] if until > now]
        return min(ends) if ends else None


def _is_free(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    try:
        zero_price = float(pricing.get("prompt", 1)) == 0 and float(pricing.get("completion", 1)) == 0
    except (TypeError, ValueError):
        zero_price = False
    return model.get("id", "").endswith(":free") or zero_price


def parse_models(payload: dict) -> dict[str, ModelInfo]:
    """Capabilities of every model with text output, indexed by id."""
    models: dict[str, ModelInfo] = {}
    for model in payload.get("data", []):
        architecture = model.get("architecture") or {}
        if "text" not in (architecture.get("output_modalities") or ["text"]):
            continue  # e.g. music/image models
        params = set(model.get("supported_parameters") or [])
        models[model["id"]] = ModelInfo(
            id=model["id"],
            vision="image" in (architecture.get("input_modalities") or []),
            structured_output=bool({"response_format", "structured_outputs"} & params),
        )
    return models


def free_model_ids(payload: dict) -> set[str]:
    return {model["id"] for model in payload.get("data", []) if _is_free(model)}


class ModelCatalog(CooldownTracker):
    def __init__(
        self,
        allowed: Sequence[str] = (),
        *,
        refresh_seconds: int = 6 * 3600,
        fetch: Callable[[], dict] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.allowed = [model.strip() for model in allowed if model and model.strip()]
        self.refresh_seconds = refresh_seconds
        self._fetch = fetch or self._fetch_from_api
        self._clock = clock
        self._known: dict[str, ModelInfo] = {}
        self._free: set[str] = set()
        self._fetched_at: float | None = None
        self._lock = threading.Lock()
        super().__init__(clock)

    def candidates(self, *, needs_vision: bool, include_cooling: bool = False) -> list[ModelInfo]:
        """Models usable now (or all of them, with include_cooling), in attempt order."""
        self._refresh_if_stale()

        with self._lock:
            if self.allowed:
                # Model missing from the catalog (or catalog unavailable): try it anyway.
                ordered = [
                    self._known.get(model) or ModelInfo(model, vision=True, structured_output=True)
                    for model in self.allowed
                ]
            else:
                free = sorted(self._free - {FREE_ROUTER})
                if FREE_ROUTER in self._free:
                    free.append(FREE_ROUTER)
                ordered = [self._known[model] for model in free if model in self._known]

        return [
            info
            for info in ordered
            if (info.vision or not needs_vision) and (include_cooling or self.is_available(info))
        ]

    def _refresh_if_stale(self) -> None:
        with self._lock:
            if self._fetched_at is not None and self._clock() - self._fetched_at < self.refresh_seconds:
                return
            # Even on failure, wait for the next cycle so we do not hammer the API.
            self._fetched_at = self._clock()

        try:
            payload = self._fetch()
            known, free = parse_models(payload), free_model_ids(payload)
        except Exception:
            logger.warning("Could not refresh model catalog; keeping previous list", exc_info=True)
            return

        with self._lock:
            self._known, self._free = known, free
        logger.info("Model catalog refreshed | free_models=%s", len(free & known.keys()))

    @staticmethod
    def _fetch_from_api() -> dict:
        # Operating system certificates (verification stays enabled).
        response = httpx.get(MODELS_URL, timeout=20, verify=ssl.create_default_context())
        response.raise_for_status()
        return response.json()
