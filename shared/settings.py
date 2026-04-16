"""Central configuration.

Every service imports `settings` from here. Service pointers are assembled from
host/port/db/user/password parts so switching between bundled and externally-
managed infrastructure is a pure env-var change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── deployment toggles ──────────────────────────────────────────────────
    use_bundled_postgres: bool = True
    use_bundled_timescale: bool = True
    use_bundled_redis: bool = True

    # ── postgres (articles) ─────────────────────────────────────────────────
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "herald"
    postgres_user: str = "herald"
    postgres_password: str = ""

    # ── timescaledb (gdelt) ─────────────────────────────────────────────────
    timescale_host: str = "timescaledb"
    timescale_port: int = 5432
    timescale_db: str = "herald_ts"
    timescale_user: str = "herald"
    timescale_password: str = ""

    # ── redis ───────────────────────────────────────────────────────────────
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db_app: int = 0
    redis_db_rsshub: int = 1
    redis_password: str = ""

    # ── mcp ─────────────────────────────────────────────────────────────────
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    mcp_transport: str = "streamable-http"

    # ── pipeline tuning ─────────────────────────────────────────────────────
    sources_file: Path = Path("/app/sources/feeds.yaml")
    extractor_consumer_group: str = "herald-extractors"
    extractor_fetch_timeout: int = 15
    dedup_ttl_days: int = 30

    # ── RSSHub ──────────────────────────────────────────────────────────────
    rsshub_host: str = "rsshub"
    rsshub_port: int = 1200

    # ── bootstrap (only used by scripts/bootstrap_db.py) ────────────────────
    bootstrap_pg_superuser: str = "postgres"
    bootstrap_pg_superpass: str = ""
    bootstrap_ts_superuser: str = "postgres"
    bootstrap_ts_superpass: str = ""

    # ── logging ─────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── constants (not env) ─────────────────────────────────────────────────
    queue_stream_name: str = Field(default="herald:article_queue", exclude=True)
    seen_urls_set: str = Field(default="herald:seen_urls", exclude=True)

    # ── computed URLs ───────────────────────────────────────────────────────
    @property
    def postgres_url(self) -> str:
        """SQLAlchemy-compatible async URL for the articles DB."""
        return self._pg_url(
            self.postgres_user,
            self.postgres_password,
            self.postgres_host,
            self.postgres_port,
            self.postgres_db,
            driver="postgresql+asyncpg",
        )

    @property
    def postgres_url_sync(self) -> str:
        """Synchronous URL (psycopg) — used by Alembic and bootstrap."""
        return self._pg_url(
            self.postgres_user,
            self.postgres_password,
            self.postgres_host,
            self.postgres_port,
            self.postgres_db,
            driver="postgresql+psycopg",
        )

    @property
    def timescale_url(self) -> str:
        return self._pg_url(
            self.timescale_user,
            self.timescale_password,
            self.timescale_host,
            self.timescale_port,
            self.timescale_db,
            driver="postgresql+asyncpg",
        )

    @property
    def timescale_url_sync(self) -> str:
        return self._pg_url(
            self.timescale_user,
            self.timescale_password,
            self.timescale_host,
            self.timescale_port,
            self.timescale_db,
            driver="postgresql+psycopg",
        )

    @property
    def rsshub_url(self) -> str:
        return f"http://{self.rsshub_host}:{self.rsshub_port}"

    @property
    def redis_url(self) -> str:
        pw = self.redis_password.strip()
        auth = f":{quote(pw)}@" if pw else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db_app}"

    @staticmethod
    def _pg_url(user: str, password: str, host: str, port: int, db: str, driver: str) -> str:
        auth = f"{quote(user)}:{quote(password)}@" if user else ""
        return f"{driver}://{auth}{host}:{port}/{db}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
