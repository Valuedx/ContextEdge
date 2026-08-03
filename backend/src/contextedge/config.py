from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = (
        "postgresql+asyncpg://contextedge:contextedge@localhost:5432/contextedge"
    )
    database_url_sync: str = (
        "postgresql://contextedge:contextedge@localhost:5432/contextedge"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_root_user: str = "contextedge"
    minio_root_password: str = "contextedge-secret"
    minio_bucket: str = "contextedge-evidence"
    minio_use_ssl: bool = False

    # Auth / JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    # Encryption
    fernet_key: str = ""

    # AI / LLM
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    google_api_key: str = ""
    location: str = "us-central1"
    google_application_credentials: str = ""
    default_llm_provider: str = "openai"
    default_classification_model: str = "gpt-4o-mini"
    default_extraction_model: str = "gpt-4o"
    default_embedding_model: str = "text-embedding-3-small"
    # E1 resilience: when set, a failed primary LLM call is retried once
    # on this model (usage recorded against the model that served).
    llm_fallback_model: str | None = None

    # --- Cost containment -------------------------------------------------
    # Every knob below bounds spend that is otherwise open-ended. Each one is
    # a ceiling, not a target: ordinary work stays well under all of them.
    #
    # LiteLLM retries. A retry is a fully billed call, so the retry count
    # multiplies the worst-case cost of any request. 5 was optimistic for a
    # transient-503 guard; 2 keeps the resilience and caps the multiplier.
    llm_num_retries: int = Field(default=2, ge=0, le=5)
    # Hard ceiling on generated tokens per call, applied on top of whatever a
    # caller passes. Output is the expensive half of the bill, and on a
    # thinking model most of it is reasoning the caller never sees.
    llm_max_output_tokens: int = Field(default=4096, ge=256)
    # Largest number of texts sent to the embedding API in one request.
    # Callers hand in whole documents' worth of chunks; without a cap a single
    # call can be arbitrarily large and fail late, after paying for it.
    embedding_max_batch_size: int = Field(default=64, ge=1, le=512)
    # Multimodal interpretation of figures in uploaded documents. On by
    # default because a support screenshot routinely carries values that
    # appear nowhere in the prose, and without this those articles cannot
    # answer the question they exist to answer. Set false to disable
    # entirely; per-document volume is bounded separately in
    # services/documents/vision.py, and spend is still gated by the
    # per-tenant budget like any other LLM call.
    document_vision_enabled: bool = True
    # Default daily caps applied to any tenant with no `tenant_llm_budgets`
    # row. Before this, "no row" meant "no limit", so a fresh tenant — the
    # normal state — was the only one running uncapped. None restores that.
    default_daily_token_limit: int | None = Field(default=2_000_000, ge=1)
    default_daily_cost_cap_usd: float | None = Field(default=25.0, ge=0.0)
    # What to do when a default cap is hit: "block" or "warn". Tenants with
    # their own budget row keep using that row's action.
    default_budget_action_on_exceed: str = "block"

    # Application
    app_env: str = "development"
    app_debug: bool = True
    app_log_level: str = "INFO"
    app_cors_origins: str = "http://localhost:3000"
    backend_port: int = 8000
    frontend_url: str = "http://localhost:3000"

    # JSON map: token string ->
    #   { "tenant_id": "uuid", "user_id": "uuid", "email": "...", "roles": ["service_account"] }
    service_tokens_json: str = "{}"

    # Scheduled retention purge behavior. "soft_purge" scrubs content in
    # place (safe default for automation); "hard_delete" removes rows and
    # cascades. See services/retention_service.py.
    retention_purge_mode: str = "soft_purge"
    # Base retention window for the scheduled archive when a tenant has no
    # active retention policy configuring its own retention_days.
    # Conservative default: a year of operational memory.
    retention_default_days: int = 365

    # Outbound notification delivery. Channels stay no-ops (logged as
    # skipped) until configured.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    notification_webhook_url: str = ""

    # PII / secret redaction at ingest. On by default — set to False only
    # for local debugging where seeing the raw payload is useful. Runs
    # before embedding + LLM extraction so PII / secrets never leave the
    # tenant boundary. Regex MVP; Presidio is a follow-up when the
    # customer has a perf profile to measure against.
    redaction_enabled: bool = True

    # Per-tenant prompt A/B overrides. JSON map:
    #     {"<tenant-uuid>": {"relevance": "v2", "episode": "v3"}}
    # Empty / absent → every tenant uses the default version registered
    # via ``register_prompt(..., default=True)``. See
    # ``contextedge/ai/prompts/__init__.py`` for resolution precedence.
    tenant_prompt_variants_json: str = "{}"


settings = Settings()

if settings.app_env != "development" and settings.jwt_secret_key == "change-me-in-production":
    raise RuntimeError(
        "JWT_SECRET_KEY must be changed from the default value in non-development environments. "
        "Set JWT_SECRET_KEY in your .env or environment variables."
    )

if settings.app_env != "development" and (
    not settings.fernet_key or "change-me" in settings.fernet_key
):
    # Without a stable Fernet key, encrypted source credentials are
    # unrecoverable garbage. Refuse to start rather than persist ciphertext
    # that can never be decrypted (see services/source_service._get_fernet).
    raise RuntimeError(
        "FERNET_KEY must be set in non-development environments. Generate one "
        "with: python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\""
    )
