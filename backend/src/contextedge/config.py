from pathlib import Path

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
