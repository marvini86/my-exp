"""Calls to free LLMs with fallback between models and providers.

Used by document extraction and by the assistant. For each call:
- models come from the catalog: `ProviderCatalog` (LLM_VISION_MODELS / LLM_TEXT_MODELS,
  several providers) or `ModelCatalog` (OpenRouter only, AGENT_ALLOWED_MODELS);
- rate limit (429), unavailability, refusal or an answer outside the schema → next
  model, and the one that failed is paused: a per-minute limit honors Retry-After;
  a daily limit lasts until the provider's reset (and pauses the whole provider when the
  quota is account-wide: OpenRouter, Cloudflare);
- models without native structured output get the schema in the prompt; the answer
  goes through a tolerant parser (JSON inside ```json``` etc.).

Final errors:
- `LLMUnavailableError`: all unavailable/rate limited (worth trying later);
- `LLMOutputError`: all answered, but none with valid output;
- `LLMRequestError`: request unacceptable for any model (e.g. 413).
"""

import json
import logging
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import TYPE_CHECKING, Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from pydantic import BaseModel, ValidationError

from core.config import AgentSettings, OpenRouterSettings
from services.extraction.model_catalog import ModelCatalog, ModelInfo

if TYPE_CHECKING:
    from core.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_RATE_LIMIT_COOLDOWN = 60
_UNAVAILABLE_COOLDOWN = 120
_NO_ENDPOINT_COOLDOWN = 3600
_REJECTED_COOLDOWN = 600
_INVALID_OUTPUT_COOLDOWN = 300

_TEMPORARY_FAILURES = {"rate_limited", "unavailable"}

JSON_ONLY_INSTRUCTION = """\
FORMATO DA RESPOSTA: responda APENAS com um objeto JSON válido, sem texto antes ou depois \
e sem blocos de código, seguindo exatamente este JSON Schema:
{schema}"""


# Daily ACCOUNT quota (every model of the provider) — for the others it is per model.
_ACCOUNT_WIDE_DAILY = {"openrouter", "cloudflare"}
_PACIFIC = "America/Los_Angeles"  # Gemini's free quota resets at Pacific midnight
_PERMANENT_COOLDOWN = 24 * 3600


class LLMUnavailableError(Exception):
    """No model available right now. `daily_limit`: the wait is due to a daily limit
    (the UI says "back at X"); otherwise it is a matter of minutes."""

    def __init__(self, message: str, delay_seconds: float, *, daily_limit: bool = False) -> None:
        super().__init__(message)
        self.delay_seconds = delay_seconds
        self.daily_limit = daily_limit


class LLMOutputError(Exception):
    """No model produced valid output."""


class LLMRequestError(Exception):
    """Request that no model would accept (e.g. content too large)."""


@dataclass
class _ModelFailure(Exception):
    kind: str  # rate_limited | unavailable | rejected | invalid_output
    cooldown_seconds: float
    detail: str
    daily: bool = False
    provider_wide: bool = False


class LLMRouter:
    def __init__(
        self,
        catalog: ModelCatalog,
        llm_factory: Callable[[str], BaseChatModel],
        *,
        empty_catalog_delay: float = 300,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.catalog = catalog
        self.llm_factory = llm_factory
        self.empty_catalog_delay = empty_catalog_delay
        self.now = now
        self._llms: dict[str, BaseChatModel] = {}
        self._structured: dict[tuple[str, type], Any] = {}

    @classmethod
    def from_settings(cls, openrouter: OpenRouterSettings, agent: AgentSettings) -> "LLMRouter":
        from langchain_openrouter import ChatOpenRouter

        def llm_factory(model: str) -> BaseChatModel:
            return ChatOpenRouter(
                model=model,
                api_key=openrouter.api_key,
                temperature=0,
                timeout=120_000,  # ms
                max_retries=0,  # no retry in the SDK: model switching happens here
                openrouter_provider={
                    # Privacy: "deny" = only providers that do not store/train on the data.
                    "data_collection": openrouter.data_collection,
                    # Only providers that support every parameter sent.
                    "require_parameters": True,
                },
            )

        logger.info(
            "LLM router configured | allowed_models=%s data_collection=%s",
            agent.allowed_models or "all free models",
            openrouter.data_collection,
        )
        return cls(ModelCatalog(agent.allowed_models), llm_factory, empty_catalog_delay=agent.rate_limit_retry_seconds)

    @classmethod
    def for_purpose(cls, settings: "Settings", *, vision: bool) -> "LLMRouter":
        """Router for extraction (vision=True) or for the assistant (vision=False).

        With LLM_VISION_MODELS / LLM_TEXT_MODELS: several providers. Without them: OpenRouter only
        (AGENT_ALLOWED_MODELS), as before.
        """
        from services.llm_providers import ProviderCatalog, available_providers, chat_model_factory

        specs = settings.llm.vision_models if vision else settings.llm.text_models
        if not specs:
            return cls.from_settings(settings.openrouter, settings.agent)

        catalog = ProviderCatalog(
            specs, vision=vision, enabled_providers=available_providers(settings.openrouter, settings.providers)
        )
        logger.info(
            "LLM router configured | purpose=%s models=%s", "vision" if vision else "text", [m.id for m in catalog.models]
        )
        return cls(
            catalog,
            chat_model_factory(settings.openrouter, settings.providers),
            empty_catalog_delay=settings.agent.rate_limit_retry_seconds,
        )

    # -- public API ----------------------------------------------------------

    def structured(
        self, messages: list, schema: type[T], *, needs_vision: bool = False, exclude: Collection[str] = ()
    ) -> tuple[T, str]:
        """(answer validated by the schema, model used). `exclude`: models not to try."""
        return self._with_fallback(lambda model: self._invoke_structured(model, messages, schema), needs_vision, exclude)

    def text(self, messages: list) -> tuple[str, str]:
        """(texto da resposta, modelo usado)."""
        return self._with_fallback(lambda model: self._invoke_text(model, messages), needs_vision=False)

    # -- fallback -------------------------------------------------------------

    def _with_fallback(
        self, call: Callable[[ModelInfo], Any], needs_vision: bool, exclude: Collection[str] = ()
    ) -> tuple[Any, str]:
        candidates = [m for m in self.catalog.candidates(needs_vision=needs_vision) if m.id not in exclude]
        if not candidates:
            everyone = [
                m for m in self.catalog.candidates(needs_vision=needs_vision, include_cooling=True) if m.id not in exclude
            ]
            delay, daily = self.catalog.wait_info(everyone) or (self.empty_catalog_delay, False)
            raise LLMUnavailableError("No AI model available right now (all on cooldown)", delay, daily_limit=daily)

        failures: list[_ModelFailure] = []
        for model in candidates:
            if not self.catalog.is_available(model):
                continue  # the whole provider was paused during this call
            try:
                return call(model), model.id
            except _ModelFailure as failure:
                failures.append(failure)
                if failure.provider_wide:
                    self.catalog.cool_down_provider(model.provider, failure.cooldown_seconds, daily=failure.daily)
                else:
                    self.catalog.cool_down(model.id, failure.cooldown_seconds, daily=failure.daily)
                logger.warning(
                    "Model failed; trying next | model=%s kind=%s detail=%s", model.id, failure.kind, failure.detail
                )

        summary = "; ".join(f"{f.kind}: {f.detail}" for f in failures)[:1500]
        if any(f.kind in _TEMPORARY_FAILURES for f in failures):
            delay, daily = self.catalog.wait_info(candidates) or (self.empty_catalog_delay, False)
            raise LLMUnavailableError(f"All AI models unavailable: {summary}", max(delay, 30), daily_limit=daily)
        raise LLMOutputError(f"No AI model produced a valid response: {summary}")

    # -- chamadas -------------------------------------------------------------

    def _invoke_structured(self, model: ModelInfo, messages: list, schema: type[T]) -> T:
        if model.structured_output:
            chain = self._structured_chain(model.id, schema)
            response = self._call(chain, messages, model)
            parsed = response.get("parsed")
            if isinstance(parsed, schema):
                return parsed
            raw_text = message_text(response.get("raw"))
        else:
            # No native structured output: the schema goes in the prompt (+ JSON mode, if the provider accepts it).
            llm = self._llm(model.id)
            runnable = llm.bind(response_format={"type": "json_object"}) if model.json_mode else llm
            response = self._call(runnable, _with_json_instruction(messages, schema), model)
            raw_text = message_text(response)

        try:
            return lenient_parse(raw_text, schema)
        except (ValidationError, ValueError) as exc:
            # Never log the content: it may contain the user's financial data.
            raise _ModelFailure(
                "invalid_output",
                _INVALID_OUTPUT_COOLDOWN,
                f"{model.id} output did not match schema ({type(exc).__name__}, {len(raw_text)} chars)",
            ) from exc

    def _invoke_text(self, model: ModelInfo, messages: list) -> str:
        text = message_text(self._call(self._llm(model.id), messages, model)).strip()
        if not text:
            raise _ModelFailure("invalid_output", _INVALID_OUTPUT_COOLDOWN, f"{model.id} returned an empty answer")
        if is_degenerate(text):
            # Never log the content (financial data): only the size.
            raise _ModelFailure(
                "invalid_output", _INVALID_OUTPUT_COOLDOWN, f"{model.id} returned a degenerate answer ({len(text)} chars)"
            )
        return text

    def _call(self, runnable: Any, messages: list, model: ModelInfo) -> Any:
        try:
            return runnable.invoke(messages)
        except Exception as exc:
            raise self._classify(exc, model) from exc

    def _llm(self, model_id: str) -> BaseChatModel:
        if model_id not in self._llms:
            self._llms[model_id] = self.llm_factory(model_id)
        return self._llms[model_id]

    def _structured_chain(self, model_id: str, schema: type) -> Any:
        key = (model_id, schema)
        if key not in self._structured:
            self._structured[key] = self._llm(model_id).with_structured_output(
                schema, method="json_schema", include_raw=True
            )
        return self._structured[key]

    def _classify(self, exc: Exception, model: ModelInfo) -> Exception:
        status = getattr(exc, "status_code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        text = str(exc).lower()
        retry_after = _retry_after_seconds(exc)

        if status == 413 and ("per minute" in text or "rate_limit" in text):
            # Groq: request larger than what is left of the tokens/min limit. It is not the content:
            # another model (or this one, shortly) will handle it.
            return _ModelFailure("rate_limited", retry_after or _RATE_LIMIT_COOLDOWN, f"{detail} (tokens per minute)")
        if status == 413:
            # Too large for any model: switching does not help.
            return LLMRequestError("Content too large for the AI model (HTTP 413)")
        if status == 429:
            if is_daily_limit(model.provider, text):
                wait = retry_after if model.provider == "groq" and retry_after else self._seconds_until_reset(model.provider)
                return _ModelFailure(
                    "rate_limited",
                    wait,
                    "daily free limit reached",
                    daily=True,
                    provider_wide=model.provider in _ACCOUNT_WIDE_DAILY,
                )
            # Per-minute limit (e.g. tokens/min on Groq): back shortly.
            return _ModelFailure("rate_limited", retry_after or _RATE_LIMIT_COOLDOWN, detail)
        if status == 403 and "free plan" in text:
            return _ModelFailure("rejected", _PERMANENT_COOLDOWN, f"{detail} (not available on the free plan)")
        if status == 404:
            return _ModelFailure("unavailable", _NO_ENDPOINT_COOLDOWN, f"{detail} (model or endpoint not found)")
        if status in (400, 422):
            return _ModelFailure("rejected", _REJECTED_COOLDOWN, detail)
        # 401/402/403, 5xx ("high demand"), timeout, network.
        return _ModelFailure("unavailable", _UNAVAILABLE_COOLDOWN, detail)

    def _seconds_until_reset(self, provider: str) -> float:
        return seconds_until_daily_reset(provider, self.now())

    def _seconds_until_utc_midnight(self) -> float:
        return self._seconds_until_reset("openrouter")


# Seen in production: the reasoning leaked ("analysis: We have a user query...") and the model got stuck
# in a loop ("Ter Ter Ter ..." thousands of times). An answer like that is a model failure: try the next one.
_REASONING_LEAK = re.compile(r"^\s*(analysis|reasoning|thinking)\s*:", re.IGNORECASE)
_REPEATED_WORD = re.compile(r"(\b\S{1,30}\b)(?:\s+\1\b){24,}")


def is_degenerate(text: str) -> bool:
    return bool(_REASONING_LEAK.match(text) or _REPEATED_WORD.search(text))


def seconds_until_daily_reset(provider: str, now: datetime) -> float:
    """Free daily quota: Gemini resets at Pacific midnight; the others at UTC midnight."""
    tz = _pacific() if provider == "gemini" else UTC
    local = now.astimezone(tz)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return (midnight - now).total_seconds() + 60


def is_daily_limit(provider: str, text: str) -> bool:
    compact = text.replace(" ", "").replace("-", "")
    if provider in ("openrouter", "gemini"):
        # OpenRouter: "free-models-per-day"; Gemini: quotaId "...RequestsPerDayPerProjectPerModel...".
        return "perday" in compact
    if provider == "groq":
        return "perday" in compact or "(rpd)" in text or "(tpd)" in text
    if provider == "cloudflare":
        return "neuron" in text or "daily" in text
    return False


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    value = headers.get("retry-after") if headers is not None else None
    try:
        seconds = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return seconds if seconds and seconds > 0 else None


def _pacific() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(_PACIFIC)
    except Exception:  # no time zone database: approximate with standard time (UTC-8)
        return timezone(timedelta(hours=-8))


def _with_json_instruction(messages: list, schema: type[BaseModel]) -> list:
    instruction = JSON_ONLY_INSTRUCTION.format(schema=json.dumps(schema.model_json_schema(), ensure_ascii=False))
    if messages and isinstance(messages[0], SystemMessage):
        system, *rest = messages
        return [SystemMessage(content=f"{system.content}\n\n{instruction}"), *rest]
    return [SystemMessage(content=instruction), *messages]


def message_text(message: Any) -> str:
    content = getattr(message, "content", "") or ""
    if isinstance(content, list):  # content blocks
        content = "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return str(content)


def lenient_parse(raw_text: str, schema: type[T]) -> T:
    """Accepts JSON wrapped in ```json ... ``` or with surrounding text; validates against the schema."""
    start, end = raw_text.find("{"), raw_text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in LLM output")
    return schema.model_validate_json(raw_text[start : end + 1])
