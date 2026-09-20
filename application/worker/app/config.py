from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Configuration for the Quorum background persistence worker."""

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/quorum"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str | None = None
    REDIS_QUEUE_KEY: str = "quorum:vote_stream"
    BATCH_SIZE: int = 50
    FLUSH_INTERVAL_SECONDS: float = 2.0
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_worker_settings() -> WorkerSettings:
    """Return cached singleton instance of worker settings."""
    return WorkerSettings()
