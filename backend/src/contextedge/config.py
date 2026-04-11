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

    # Application
    app_env: str = "development"
    app_debug: bool = True
    app_log_level: str = "INFO"
    app_cors_origins: str = "http://localhost:3000"
    backend_port: int = 8000
    frontend_url: str = "http://localhost:3000"

    # JSON map: token string -> { "tenant_id": "uuid", "user_id": "uuid", "email": "...", "roles": ["service_account"] }
    service_tokens_json: str = "{}"


settings = Settings()
