"""Idempotent database bootstrap.

Creates the application DBs, roles, and required extensions on both the
articles Postgres and TimescaleDB targets, then runs Alembic migrations
against whichever target is applicable in the current release.

Works identically against bundled docker-compose containers and against
externally-managed managed-Postgres instances — only the host/port/creds
in `.env` differ. Superuser credentials for the CREATE DATABASE / CREATE
ROLE steps are taken from:

    BOOTSTRAP_PG_SUPERUSER / BOOTSTRAP_PG_SUPERPASS
    BOOTSTRAP_TS_SUPERUSER / BOOTSTRAP_TS_SUPERPASS

Usage:
    uv run python scripts/bootstrap_db.py
    uv run python scripts/bootstrap_db.py --skip-timescale
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import psycopg
from psycopg import sql

# Allow running as `python scripts/bootstrap_db.py`
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.settings import settings  # noqa: E402


def _wait_for_server(host: str, port: int, user: str, password: str, timeout: int = 60) -> None:
    """Block until the Postgres server accepts connections (useful right after compose up)."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(
                host=host, port=port, user=user, password=password, dbname="postgres", connect_timeout=3
            ):
                return
        except psycopg.OperationalError as e:
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {host}:{port} — last error: {last_err}")


def _ensure_role(cur, role: str, password: str) -> None:
    # Postgres rejects bind params in CREATE/ALTER ROLE ... PASSWORD clauses,
    # so the password is safely quoted via psycopg.sql.Literal instead.
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    if cur.fetchone():
        cur.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        print(f"  role {role!r} already exists — password reset")
    else:
        cur.execute(
            sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        print(f"  created role {role!r}")


def _ensure_database(cur, dbname: str, owner: str) -> None:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
    if cur.fetchone():
        print(f"  database {dbname!r} already exists")
        return
    cur.execute(
        sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(dbname), sql.Identifier(owner)
        )
    )
    print(f"  created database {dbname!r} owned by {owner!r}")


def _ensure_extensions(host: str, port: int, super_user: str, super_pass: str, dbname: str, extensions: list[str]) -> None:
    """Install extensions inside the target DB (must connect to that DB, as superuser)."""
    with psycopg.connect(
        host=host, port=port, user=super_user, password=super_pass, dbname=dbname, autocommit=True
    ) as conn, conn.cursor() as cur:
        for ext in extensions:
            cur.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext)))
            print(f"  ensured extension {ext!r}")


def _grant_ownership(host: str, port: int, super_user: str, super_pass: str, dbname: str, role: str) -> None:
    """Make sure the app role owns the schema so migrations can create objects."""
    with psycopg.connect(
        host=host, port=port, user=super_user, password=super_pass, dbname=dbname, autocommit=True
    ) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(role))
        )
        cur.execute(
            sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                sql.Identifier(dbname), sql.Identifier(role)
            )
        )
        print(f"  granted schema public + db privileges to {role!r}")


def bootstrap_target(
    *,
    label: str,
    host: str,
    port: int,
    super_user: str,
    super_pass: str,
    app_db: str,
    app_user: str,
    app_pass: str,
    extensions: list[str],
) -> None:
    print(f"\n▶ {label}: {host}:{port} (db={app_db}, user={app_user})")
    _wait_for_server(host, port, super_user, super_pass)
    with psycopg.connect(
        host=host, port=port, user=super_user, password=super_pass, dbname="postgres", autocommit=True
    ) as conn, conn.cursor() as cur:
        _ensure_role(cur, app_user, app_pass)
        _ensure_database(cur, app_db, app_user)
    _ensure_extensions(host, port, super_user, super_pass, app_db, extensions)
    _grant_ownership(host, port, super_user, super_pass, app_db, app_user)
    print(f"✓ {label} ready")


def run_alembic(db_tag: str) -> None:
    """Invoke Alembic as a module so we use the same interpreter/venv."""
    import subprocess
    import sys

    print(f"\n▶ alembic upgrade head  (-x db={db_tag})")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db={db_tag}", "upgrade", "head"],
        cwd=str(ROOT),
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"alembic failed for db={db_tag} (exit {result.returncode})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap HERALD databases.")
    parser.add_argument("--skip-postgres", action="store_true")
    parser.add_argument("--skip-timescale", action="store_true")
    parser.add_argument("--skip-migrate", action="store_true")
    args = parser.parse_args()

    if not args.skip_postgres:
        bootstrap_target(
            label="articles postgres",
            host=settings.postgres_host,
            port=settings.postgres_port,
            super_user=settings.bootstrap_pg_superuser,
            super_pass=settings.bootstrap_pg_superpass,
            app_db=settings.postgres_db,
            app_user=settings.postgres_user,
            app_pass=settings.postgres_password,
            extensions=["pgcrypto", "pg_trgm"],
        )
        if not args.skip_migrate:
            run_alembic("postgres")

    if not args.skip_timescale:
        bootstrap_target(
            label="timescaledb",
            host=settings.timescale_host,
            port=settings.timescale_port,
            super_user=settings.bootstrap_ts_superuser,
            super_pass=settings.bootstrap_ts_superpass,
            app_db=settings.timescale_db,
            app_user=settings.timescale_user,
            app_pass=settings.timescale_password,
            extensions=["pgcrypto", "timescaledb"],
        )
        # GDELT migration (0002) is delivered in a later release; running
        # `alembic -x db=timescale upgrade head` today is a no-op and safe.
        if not args.skip_migrate:
            run_alembic("timescale")

    print("\n✓ bootstrap complete")


if __name__ == "__main__":
    main()
