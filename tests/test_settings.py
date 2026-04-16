"""Sanity checks on the central config object — URL composition is the bit
most likely to silently break when env vars get refactored."""

from __future__ import annotations

from shared.settings import Settings


def test_postgres_url_composes_with_credentials():
    s = Settings(
        postgres_host="db.internal",
        postgres_port=6543,
        postgres_db="herald_app",
        postgres_user="alice",
        postgres_password="p@ss/word",
    )
    url = s.postgres_url
    assert url.startswith("postgresql+asyncpg://")
    assert "alice" in url
    # special chars must be URL-encoded
    assert "p%40ss%2Fword" in url
    assert "db.internal:6543/herald_app" in url


def test_redis_url_no_password():
    s = Settings(redis_host="cache", redis_port=6379, redis_db_app=2, redis_password="")
    assert s.redis_url == "redis://cache:6379/2"


def test_redis_url_with_password():
    s = Settings(redis_host="cache", redis_password="hunter2")
    assert "hunter2" in s.redis_url


def test_sync_url_uses_psycopg_driver():
    s = Settings(postgres_password="x")
    assert s.postgres_url_sync.startswith("postgresql+psycopg://")


def test_external_db_swap_is_pure_config():
    """Demonstrate the swappability requirement: changing host/port/user
    propagates through the URL without code changes."""
    bundled = Settings(postgres_host="postgres", postgres_password="x")
    external = Settings(postgres_host="db.example.internal", postgres_port=15432, postgres_password="x")
    assert "postgres:5432" in bundled.postgres_url
    assert "db.example.internal:15432" in external.postgres_url
