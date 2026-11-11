from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# api/.env, regardless of the directory the application is started from.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class DbSettings(BaseSettings):
    host: str
    port: int
    user: str
    password: str
    name: str

    model_config = SettingsConfigDict(
        env_prefix="DB_",
        env_file=ENV_FILE,
        extra="ignore",
    )

def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class QdrantSettings(BaseSettings):
    url: str
    api_key: str | None = None
    collection: str = "expense_documents"
    # Prototype leftover (local embeddings); unused. See EMBEDDING_MODELS.
    embedding_model: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="QDRANT_",
        env_file=ENV_FILE,
        extra="ignore",
    )


class EmbeddingSettings(BaseSettings):
    """Embedding models (OpenRouter), in order of preference.

    Each model becomes a named vector in the collection: search uses the first
    available one. Changing the list requires recreating the collection (reindexing).
    """

    models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["gemini:gemini-embedding-001", "cloudflare:@cf/baai/bge-m3"]
    )
    batch_size: int = Field(default=32, ge=1, le=256)
    chunk_size: int = Field(default=1000, ge=200)
    chunk_overlap: int = Field(default=150, ge=0)

    model_config = SettingsConfigDict(
        env_prefix="EMBEDDING_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    _split_models = field_validator("models", mode="before")(_split_csv)

class OpenRouterSettings(BaseSettings):
    api_key: str
    # Prototype leftover; the models come from AGENT_ALLOWED_MODELS.
    model: str | None = None
    # "deny": only providers that do not store/train on the data.
    # Free models (":free") usually require "allow".
    data_collection: Literal["allow", "deny"] = "deny"

    model_config = SettingsConfigDict(
        env_prefix="OPENROUTER_",
        env_file=ENV_FILE,
        extra="ignore",
    )

class AgentSettings(BaseSettings):
    """AI models used by the agents (document extraction).

    AGENT_ALLOWED_MODELS: comma-separated list, in order of preference.
    When a model hits a limit/becomes unavailable, the next one is used.
    Empty → every free OpenRouter model.
    """

    allowed_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Delay when every model is rate limited (the job is postponed without using an attempt).
    rate_limit_retry_seconds: int = Field(default=300, ge=30)

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    _split_models = field_validator("allowed_models", mode="before")(_split_csv)


class ProviderKeys(BaseSettings):
    """AI provider keys (besides OPENROUTER_API_KEY, in OpenRouterSettings)."""

    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    cloudflare_api_key: SecretStr | None = None
    cloudflare_account_id: str | None = None

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")


class LlmSettings(BaseSettings):
    """Models per use, "provider:model" in order of preference (e.g. groq:openai/gpt-oss-120b).

    LLM_VISION_MODELS: document extraction (worker). LLM_TEXT_MODELS: assistant.
    Separate lists so the chat does not consume the worker's quota (and vice versa).
    Empty → previous behavior (OpenRouter only, AGENT_ALLOWED_MODELS).
    """

    vision_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    text_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # How many extra models the extraction tries when the result is inconsistent
    # (items sum ≠ total, items read ≠ items printed).
    extraction_quality_retries: int = Field(default=2, ge=0, le=5)

    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=ENV_FILE, extra="ignore")

    _split_vision = field_validator("vision_models", mode="before")(_split_csv)
    _split_text = field_validator("text_models", mode="before")(_split_csv)


class StorageSettings(BaseSettings):
    """Azure Blob Storage. Variables: FILES_CONTAINER, STG_ACCOUNT_NAME, STG_ACCOUNT_KEY."""

    # "container" or "container/prefix" (e.g. "myexp/dev"). Azure container names
    # do not accept "/", so whatever comes after it becomes the blob prefix.
    files_container: str
    stg_account_name: str
    stg_account_key: SecretStr

    download_url_ttl_seconds: int = Field(default=600, ge=60, le=3600)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        extra="ignore",
    )

    @property
    def container_name(self) -> str:
        return self.files_container.strip("/").split("/", 1)[0]

    @property
    def blob_prefix(self) -> str:
        parts = self.files_container.strip("/").split("/", 1)
        return parts[1].strip("/") if len(parts) == 2 else ""


class UploadSettings(BaseSettings):
    max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)

    model_config = SettingsConfigDict(
        env_prefix="UPLOAD_",
        env_file=ENV_FILE,
        extra="ignore",
    )


class WorkerSettings(BaseSettings):
    """Ingestion worker. Uses the myexp_worker role (WORKER_DB_USER/PASSWORD);
    host, port and database come from DB_*. Optional for the API."""

    db_user: str | None = None
    db_password: SecretStr | None = None

    poll_interval_seconds: float = Field(default=5.0, gt=0)
    # A job "processing" for longer than this is considered abandoned.
    lock_timeout_seconds: int = Field(default=900, ge=60)
    # Wait before attempt N: base * factor^(N-1) → 60s, 5min, 25min.
    retry_base_seconds: int = Field(default=60, ge=1)
    retry_factor: int = Field(default=5, ge=1)

    model_config = SettingsConfigDict(
        env_prefix="WORKER_",
        env_file=ENV_FILE,
        extra="ignore",
    )


class AuthSettings(BaseSettings):
    """Google login and API sessions.

    AUTH_GOOGLE_CLIENT_IDS: OAuth Client IDs (Google Cloud Console) accepted
    as the ID token "aud", comma-separated (web, mobile...).
    Empty → Google login disabled (503).
    AUTH_ADMIN_EMAILS: platform administrators (manage who can
    sign in). Access is CLOSED: only admins, e-mails they allowed
    and people invited to some account can sign in.
    """

    google_client_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    admin_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)

    access_token_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)

    model_config = SettingsConfigDict(
        env_prefix="AUTH_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    _split_client_ids = field_validator("google_client_ids", mode="before")(_split_csv)

    @field_validator("admin_emails", mode="before")
    @classmethod
    def _split_emails(cls, value: object) -> object:
        value = _split_csv(value)
        return [str(email).lower() for email in value] if isinstance(value, list) else value

    def is_admin(self, email: str | None) -> bool:
        return bool(email) and email.lower() in self.admin_emails


class Settings(BaseSettings):
    # Default "production" (fail closed): dev features, such as
    # header authentication, only turn on with an explicit ENVIRONMENT=development.
    environment: Literal["development", "production"] = "production"
    log_level: str = "INFO"

    # Allowed frontend origins (CORS), e.g. "http://localhost:3000".
    # Empty → no browser origin (server-side calls still work).
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # /docs and /openapi.json. None → development only.
    docs_enabled: bool | None = None
    # In-memory rate limiting (per process). Can be disabled for load tests.
    rate_limit_enabled: bool = True

    db: DbSettings = Field(default_factory=DbSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    providers: ProviderKeys = Field(default_factory=ProviderKeys)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    _split_origins = field_validator("cors_origins", mode="before")(_split_csv)

    @property
    def docs_visible(self) -> bool:
        return self.environment == "development" if self.docs_enabled is None else self.docs_enabled


settings = Settings()