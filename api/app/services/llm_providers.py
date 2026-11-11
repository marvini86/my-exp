"""Several free LLM providers, each with its own quota.

Configuration (.env), "provider:model" in order of preference:
    LLM_VISION_MODELS=gemini:gemini-3.5-flash-lite,groq:qwen/qwen3.8-27b,...
    LLM_TEXT_MODELS=groq:openai/gpt-oss-120b,cloudflare:@cf/openai/gpt-oss-120b,...

Gemini, Groq and Cloudflare Workers AI expose an OpenAI-compatible API (a
single client, `ChatOpenAI`, with each one's base URL); OpenRouter keeps
its own client (providers' privacy policy). A provider without a
key in .env is skipped with a warning.

Quotas (the router pauses whatever hit a limit and moves on to the next one):
- OpenRouter and Cloudflare: daily ACCOUNT quota → pauses the whole provider.
- Gemini and Groq: per-MODEL quota → pauses only the model.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from core.config import OpenRouterSettings, ProviderKeys
from services.extraction.model_catalog import CooldownTracker, ModelInfo

logger = logging.getLogger(__name__)

OPENAI_COMPATIBLE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "cloudflare": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
}
PROVIDERS = {*OPENAI_COMPATIBLE_URLS, "openrouter"}

# Without this some providers truncate the answer (Cloudflare uses 256 tokens by default),
# and a receipt with many items does not fit.
MAX_OUTPUT_TOKENS = 8192
# Groq counts the reserved max_tokens against the 8,000 tokens/min limit: with 8,192, every
# request overflows (413). 3,000 fits and still leaves room for a receipt with dozens of items.
MAX_OUTPUT_TOKENS_BY_PROVIDER = {"groq": 3000}
# Larger Cloudflare models can take up to 2 min per image.
REQUEST_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}"


def parse_route(spec: str) -> ModelRoute:
    """"groq:openai/gpt-oss-120b" → (groq, openai/gpt-oss-120b). Only the 1st ":" splits (models have ":free")."""
    provider, sep, model = spec.strip().partition(":")
    provider = provider.strip().lower()
    if not sep or not model.strip() or provider not in PROVIDERS:
        raise ValueError(f"Rota de modelo inválida: {spec!r} (use provedor:modelo; provedores: {sorted(PROVIDERS)})")
    return ModelRoute(provider, model.strip())


def available_providers(openrouter: OpenRouterSettings, keys: ProviderKeys) -> set[str]:
    providers = {"openrouter"} if openrouter.api_key else set()
    if keys.gemini_api_key:
        providers.add("gemini")
    if keys.groq_api_key:
        providers.add("groq")
    if keys.cloudflare_api_key and keys.cloudflare_account_id:
        providers.add("cloudflare")
    return providers


class ProviderCatalog(CooldownTracker):
    """Fixed list of routes (vision OR text), with pauses per model and per provider."""

    def __init__(
        self,
        specs: Sequence[str],
        *,
        vision: bool,
        enabled_providers: set[str],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(clock)
        self.vision = vision
        self.models: list[ModelInfo] = []
        for spec in specs:
            route = parse_route(spec)
            if route.provider not in enabled_providers:
                logger.warning("Model route ignored: no API key for provider | route=%s", route.id)
                continue
            self.models.append(
                ModelInfo(
                    id=route.id,
                    vision=vision,
                    # OpenRouter: structured output nativo (json_schema). Demais: schema no
                    # prompt + response_format json_object (wider support across providers).
                    structured_output=route.provider == "openrouter",
                    provider=route.provider,
                    json_mode=route.provider != "openrouter",
                )
            )

    def candidates(self, *, needs_vision: bool, include_cooling: bool = False) -> list[ModelInfo]:
        if needs_vision and not self.vision:
            return []
        return [m for m in self.models if include_cooling or self.is_available(m)]


def chat_model_factory(openrouter: OpenRouterSettings, keys: ProviderKeys) -> Callable[[str], BaseChatModel]:
    """Creates the client for a "provider:model"."""

    def build(model_id: str) -> BaseChatModel:
        route = parse_route(model_id)
        if route.provider == "openrouter":
            from langchain_openrouter import ChatOpenRouter

            return ChatOpenRouter(
                model=route.model,
                api_key=openrouter.api_key,
                temperature=0,
                timeout=REQUEST_TIMEOUT_SECONDS * 1000,  # ms
                max_retries=0,
                openrouter_provider={"data_collection": openrouter.data_collection, "require_parameters": True},
            )

        from langchain_openai import ChatOpenAI

        secret = {"gemini": keys.gemini_api_key, "groq": keys.groq_api_key, "cloudflare": keys.cloudflare_api_key}[
            route.provider
        ]
        return ChatOpenAI(
            model=route.model,
            base_url=OPENAI_COMPATIBLE_URLS[route.provider].format(account_id=keys.cloudflare_account_id),
            api_key=secret.get_secret_value() if secret else None,
            temperature=0,
            max_tokens=MAX_OUTPUT_TOKENS_BY_PROVIDER.get(route.provider, MAX_OUTPUT_TOKENS),
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,  # no retry in the SDK: the router switches models
        )

    return build
