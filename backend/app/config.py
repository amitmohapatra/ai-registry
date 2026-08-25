"""Application settings. Everything overridable via environment variables."""
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "AI Registry"
    database_url: str = "sqlite+aiosqlite:///./registry.db"
    jwt_secret: str = "change-me-in-prod"
    jwt_ttl_seconds: int = 8 * 3600
    secret_key: str = "change-me-32-bytes-secret-key!!"  # for encrypting channel configs
    bootstrap_admin_email: str = "admin@registry.dev"
    bootstrap_admin_password: str = "admin"
    redis_url: str = ""            # optional: enables Redis cache + bus
    manifest_cache_ttl: int = 300
    embedding_provider: str = "fastembed"  # fastembed | hashing | openai (falls back to hashing)
    embedding_api_key: str = ""
    embedding_model: str = ""              # default: BAAI/bge-base-en-v1.5 for fastembed
    reranker: str = "fastembed"            # fastembed | none — cross-encoder pass on top pairs
    reranker_model: str = "jinaai/jina-reranker-v1-turbo-en"
    similarity_threshold: float = 0.50    # duplicates cutoff (installation default)
    bifrost_url: str = ""                 # e.g. http://localhost:8080/v1 (OpenAI-compatible)
    bifrost_key: str = ""                 # Bifrost virtual key (installation default)
    llm_model: str = "anthropic/claude-sonnet-4-5"

    model_config = {"env_prefix": "REGISTRY_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
