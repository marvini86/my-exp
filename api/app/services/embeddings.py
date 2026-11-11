"""Free embeddings from several providers (OpenAI-compatible API) with fallback.

EMBEDDING_MODELS: "provider:model" (gemini, cloudflare, openrouter); an id without a
provider belongs to OpenRouter (backward compatibility). E.g.:
    EMBEDDING_MODELS=gemini:gemini-embedding-001,cloudflare:@cf/baai/bge-m3

Vectors from different models are NOT comparable. That is why each model in
`EMBEDDING_MODELS` becomes a *named vector* in Qdrant:

- indexing: produces the vector of every model available at the time;
- search: uses the first available model and searches its vector.

If no model responds, indexing is postponed (the job goes back to the queue without
using an attempt) and search answers 503.
"""

import logging
import re
import ssl
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import httpx
from langchain_core.embeddings import Embeddings

from core.config import EmbeddingSettings, OpenRouterSettings, ProviderKeys
from services.llm_router import is_daily_limit, seconds_until_daily_reset

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1"
PROVIDER_URLS = {
    "openrouter": API_URL,
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "cloudflare": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
}
# Gemini produces 3072 dimensions by default; 768 is enough for search and takes 4x less space in Qdrant.
GEMINI_DIMENSIONS = 768

_RATE_LIMIT_COOLDOWN = 60
_UNAVAILABLE_COOLDOWN = 120


class EmbeddingUnavailableError(Exception):
    """Embedding model (or every embedding model) currently unavailable."""

    def __init__(self, message: str, cooldown_seconds: float = _UNAVAILABLE_COOLDOWN) -> None:
        super().__init__(message)
        self.cooldown_seconds = cooldown_seconds


def vector_name(model_id: str) -> str:
    """Nome do vetor no Qdrant: 'liquid/lfm-2.5-embedding-350m:free' → 'lfm_2_5_embedding_350m'."""
    name = model_id.split("/", 1)[-1].removesuffix(":free")
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class OpenAICompatibleEmbeddings(Embeddings):
    """An embedding model from an OpenAI-compatible API (/embeddings), behind the LangChain interface."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        provider: str = "openrouter",
        base_url: str = API_URL,
        extra_body: dict | None = None,
        batch_size: int = 32,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.vector_name = vector_name(model)
        self.extra_body = extra_body or {}
        self.batch_size = batch_size
        self._client = client or httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
            verify=ssl.create_default_context(),  # system certificates
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed(texts[start : start + self.batch_size]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        try:
            response = self._client.post(
                "/embeddings",
                json={"model": self.model, "input": inputs, **self.extra_body},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailableError(f"{self.model}: {type(exc).__name__}") from exc

        if response.status_code == 429:
            daily = is_daily_limit(self.provider, response.text.lower())
            raise EmbeddingUnavailableError(
                f"{self.model}: HTTP 429" + (" (daily free limit)" if daily else ""),
                seconds_until_daily_reset(self.provider, datetime.now(UTC)) if daily else _RATE_LIMIT_COOLDOWN,
            )
        if response.status_code != 200:
            # 404 (no provider for the data policy), 402, 5xx...
            raise EmbeddingUnavailableError(f"{self.model}: HTTP {response.status_code}")

        try:
            items = response.json()["data"]
            # Gemini omits "index" on the first item: without it, the position is used.
            def position(pair: tuple[int, dict]) -> int:
                index = pair[1].get("index")
                return pair[0] if index is None else index

            vectors = [item["embedding"] for _, item in sorted(enumerate(items), key=position)]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingUnavailableError(f"{self.model}: invalid response") from exc

        if len(vectors) != len(inputs):
            raise EmbeddingUnavailableError(f"{self.model}: expected {len(inputs)} vectors, got {len(vectors)}")
        return vectors


class OpenRouterEmbeddings(OpenAICompatibleEmbeddings):
    """OpenRouter embedding (honors the providers' data collection policy)."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        data_collection: str = "deny",
        batch_size: int = 32,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            model,
            api_key,
            provider="openrouter",
            extra_body={"provider": {"data_collection": data_collection}},
            batch_size=batch_size,
            client=client,
        )
        self.data_collection = data_collection


def _split_spec(spec: str) -> tuple[str, str]:
    """"gemini:gemini-embedding-001" → (gemini, ...); an id without a known provider → OpenRouter."""
    provider, sep, model = spec.strip().partition(":")
    if sep and provider.lower() in PROVIDER_URLS and model:
        return provider.lower(), model.strip()
    return "openrouter", spec.strip()


def build_embedding_model(
    spec: str, openrouter: OpenRouterSettings, keys: ProviderKeys | None, batch_size: int
) -> Embeddings | None:
    """Model client, or None if the provider key is missing (the route is skipped)."""
    provider, model = _split_spec(spec)
    if provider == "openrouter":
        return OpenRouterEmbeddings(model, openrouter.api_key, data_collection=openrouter.data_collection, batch_size=batch_size)
    if keys is None:
        return None
    if provider == "gemini" and keys.gemini_api_key:
        return OpenAICompatibleEmbeddings(
            model,
            keys.gemini_api_key.get_secret_value(),
            provider="gemini",
            base_url=PROVIDER_URLS["gemini"],
            extra_body={"dimensions": GEMINI_DIMENSIONS},
            batch_size=batch_size,
        )
    if provider == "cloudflare" and keys.cloudflare_api_key and keys.cloudflare_account_id:
        return OpenAICompatibleEmbeddings(
            model,
            keys.cloudflare_api_key.get_secret_value(),
            provider="cloudflare",
            base_url=PROVIDER_URLS["cloudflare"].format(account_id=keys.cloudflare_account_id),
            batch_size=batch_size,
        )
    return None


class EmbeddingRouter:
    def __init__(
        self,
        models: Sequence[Embeddings],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not models:
            raise ValueError("At least one embedding model is required")
        self.models = list(models)
        self._clock = clock
        self._cooldown_until: dict[str, float] = {}
        self._dimensions: dict[str, int] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(
        cls, openrouter: OpenRouterSettings, config: EmbeddingSettings, keys: ProviderKeys | None = None
    ) -> "EmbeddingRouter":
        models = []
        for spec in config.models:
            model = build_embedding_model(spec, openrouter, keys, config.batch_size)
            if model is None:
                logger.warning("Embedding model ignored: no API key for provider | model=%s", spec)
            else:
                models.append(model)
        logger.info("Embedding models configured | models=%s", [m.vector_name for m in models])
        return cls(models)

    @property
    def vector_names(self) -> list[str]:
        return [model.vector_name for model in self.models]

    def dimensions(self) -> dict[str, int]:
        """Dimension of each model (discovered with one call; unavailable models are left out)."""
        for model in self._available():
            if model.vector_name not in self._dimensions:
                try:
                    self._dimensions[model.vector_name] = len(model.embed_query("dimension probe"))
                except EmbeddingUnavailableError as exc:
                    self._cool_down(model, exc)
        return dict(self._dimensions)

    def embed_documents(self, texts: list[str]) -> dict[str, list[list[float]]]:
        """Vectors from every available model: {vector_name: [vector per text]}."""
        result: dict[str, list[list[float]]] = {}
        errors: list[EmbeddingUnavailableError] = []

        for model in self._available():
            try:
                result[model.vector_name] = model.embed_documents(texts)
                self._dimensions.setdefault(model.vector_name, len(result[model.vector_name][0]))
            except EmbeddingUnavailableError as exc:
                errors.append(exc)
                self._cool_down(model, exc)

        if not result:
            raise self._all_failed(errors)
        return result

    def embed_query(self, text: str) -> tuple[str, list[float]]:
        """(vector_name, vector) of the first available model."""
        errors: list[EmbeddingUnavailableError] = []
        for model in self._available():
            try:
                return model.vector_name, model.embed_query(text)
            except EmbeddingUnavailableError as exc:
                errors.append(exc)
                self._cool_down(model, exc)
        raise self._all_failed(errors)

    def _available(self) -> list[Embeddings]:
        now = self._clock()
        with self._lock:
            return [m for m in self.models if self._cooldown_until.get(m.vector_name, 0) <= now]

    def _cool_down(self, model: Embeddings, exc: EmbeddingUnavailableError) -> None:
        with self._lock:
            self._cooldown_until[model.vector_name] = self._clock() + exc.cooldown_seconds
        logger.warning("Embedding model unavailable | %s | cooldown=%ss", exc, int(exc.cooldown_seconds))

    def _all_failed(self, errors: list[EmbeddingUnavailableError]) -> EmbeddingUnavailableError:
        if not errors:
            # All of them were already cooling down: wait for the first one to become available.
            now = self._clock()
            wait = min(self._cooldown_until.get(m.vector_name, now) for m in self.models) - now
            return EmbeddingUnavailableError("All embedding models on cooldown", max(wait, 30))
        return EmbeddingUnavailableError(
            "All embedding models unavailable: " + "; ".join(str(e) for e in errors),
            max(min(e.cooldown_seconds for e in errors), 30),
        )
