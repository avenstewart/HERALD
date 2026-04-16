"""Alembic environment.

The target database is chosen via ``alembic -x db=postgres`` (default) or
``alembic -x db=timescale``. Each target has its own MetaData object in
``shared.models`` so migrations operate on an unambiguous schema.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from shared import models
from shared.settings import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_target() -> tuple[str, object]:
    x_args = context.get_x_argument(as_dictionary=True)
    db = (x_args.get("db") or "postgres").lower()
    if db == "postgres":
        return settings.postgres_url_sync, models.articles_metadata
    if db == "timescale":
        return settings.timescale_url_sync, models.gdelt_metadata
    raise ValueError(f"Unknown db target: {db!r}. Use db=postgres or db=timescale.")


def run_migrations_offline() -> None:
    url, target_metadata = _resolve_target()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url, target_metadata = _resolve_target()
    config.set_main_option("sqlalchemy.url", url)
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
