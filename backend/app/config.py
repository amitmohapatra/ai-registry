"""Application settings. Everything overridable via environment variables
(REGISTRY_ prefix — e.g. REGISTRY_DB_POOL_SIZE=25). Deployment knobs live
here; similarity BEHAVIOR knobs (thresholds, package counts, retention) are
runtime data instead: see tuning.py, editable by the super admin via the API."""
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
    reranker_model: str = "BAAI/bge-reranker-base"   # permissive license, best equivalence ordering
    similarity_threshold: float = 0.50    # duplicates cutoff (installation default)

    # database connection pool (ignored for SQLite :memory:, which uses StaticPool)
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # in-process caches: entries, not bytes
    pair_cache_cap: int = 16384      # cross-encoder pair scores
    preview_cache_cap: int = 256     # editor similar-preview results
    resolve_cache_cap: int = 64      # overlap pair resolutions
    report_cache_cap: int = 16       # materialized overlap reports

    # how many similarity computations may run concurrently (CPU-bound)
    score_concurrency: int = 1

    # pagination contract: list endpoints default to page_default rows and
    # refuse more than page_max; totals travel in the X-Total-Count header
    page_default: int = 100
    page_max: int = 500

    model_config = {"env_prefix": "REGISTRY_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


# import-time aliases for values that parameterize decorators and class
# bodies (Query defaults, cache classes) — still env-driven via Settings
_s = get_settings()
DB_POOL_SIZE = _s.db_pool_size
DB_MAX_OVERFLOW = _s.db_max_overflow
PAIR_CACHE_CAP = _s.pair_cache_cap
PREVIEW_CACHE_CAP = _s.preview_cache_cap
RESOLVE_CACHE_CAP = _s.resolve_cache_cap
REPORT_CACHE_CAP = _s.report_cache_cap
SCORE_CONCURRENCY = _s.score_concurrency
PAGE_DEFAULT = _s.page_default
PAGE_MAX = _s.page_max
