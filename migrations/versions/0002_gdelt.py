"""GDELT hypertables + retention + state

Revision ID: 0002_gdelt
Revises: 0001_articles
Create Date: 2026-04-16

This migration is designed to run against the TimescaleDB target:
    alembic -x db=timescale upgrade head

Running it against the articles Postgres is a no-op because that instance
does not have the TimescaleDB extension. The migration is guarded so it
errors clearly if the extension is missing.
"""
from __future__ import annotations

from alembic import op

revision = "0002_gdelt"
down_revision = "0001_articles"
branch_labels = None
depends_on = None


def _is_timescale() -> bool:
    conn = op.get_bind()
    row = conn.exec_driver_sql(
        "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'"
    ).first()
    return row is not None


def upgrade() -> None:
    if not _is_timescale():
        # Safe no-op when run against a non-Timescale target (e.g. the articles DB).
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── gdelt_events ─────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE gdelt_events (
            event_id         TEXT NOT NULL,
            event_date       TIMESTAMPTZ NOT NULL,
            actor1_name      TEXT,
            actor1_country   TEXT,
            actor1_type      TEXT,
            actor2_name      TEXT,
            actor2_country   TEXT,
            actor2_type      TEXT,
            cameo_code       TEXT NOT NULL,
            cameo_root_code  TEXT NOT NULL,
            cameo_label      TEXT,
            goldstein_scale  DOUBLE PRECISION,
            num_mentions     INTEGER,
            num_sources      INTEGER,
            num_articles     INTEGER,
            avg_tone         DOUBLE PRECISION,
            geo_fullname     TEXT,
            geo_country      TEXT,
            geo_lat          DOUBLE PRECISION,
            geo_lon          DOUBLE PRECISION,
            source_url       TEXT,
            ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (event_id, event_date)
        )
        """
    )
    op.execute(
        "SELECT create_hypertable('gdelt_events', 'event_date', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
    )
    op.execute(
        "CREATE INDEX idx_gdelt_events_date_root ON gdelt_events "
        "(event_date DESC, cameo_root_code)"
    )
    op.execute(
        "CREATE INDEX idx_gdelt_events_countries ON gdelt_events "
        "(actor1_country, actor2_country, event_date DESC)"
    )
    op.execute(
        "CREATE INDEX idx_gdelt_events_goldstein ON gdelt_events "
        "(goldstein_scale, event_date DESC)"
    )

    # ── gdelt_gkg ────────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE gdelt_gkg (
            record_id         TEXT NOT NULL,
            record_date       TIMESTAMPTZ NOT NULL,
            source_url        TEXT NOT NULL,
            source_name       TEXT,
            themes            TEXT[],
            locations         TEXT[],
            persons           TEXT[],
            organizations     TEXT[],
            tone              DOUBLE PRECISION,
            positive_score    DOUBLE PRECISION,
            negative_score    DOUBLE PRECISION,
            polarity          DOUBLE PRECISION,
            activity_density  DOUBLE PRECISION,
            word_count        INTEGER,
            gcam_scores       JSONB,
            ingested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (record_id, record_date)
        )
        """
    )
    op.execute(
        "SELECT create_hypertable('gdelt_gkg', 'record_date', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
    )
    op.execute("CREATE INDEX idx_gdelt_gkg_date ON gdelt_gkg (record_date DESC)")
    op.execute("CREATE INDEX idx_gdelt_gkg_themes ON gdelt_gkg USING GIN (themes)")
    op.execute("CREATE INDEX idx_gdelt_gkg_persons ON gdelt_gkg USING GIN (persons)")
    op.execute("CREATE INDEX idx_gdelt_gkg_orgs ON gdelt_gkg USING GIN (organizations)")
    op.execute("CREATE INDEX idx_gdelt_gkg_locations ON gdelt_gkg USING GIN (locations)")
    op.execute(
        "CREATE INDEX idx_gdelt_gkg_tone ON gdelt_gkg (tone, record_date DESC)"
    )

    # ── retention policies (2 years) ─────────────────────────────────────────
    op.execute(
        "SELECT add_retention_policy('gdelt_events', INTERVAL '2 years', "
        "if_not_exists => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('gdelt_gkg', INTERVAL '2 years', "
        "if_not_exists => TRUE)"
    )

    # ── singleton state table for the ingestor ───────────────────────────────
    op.execute(
        """
        CREATE TABLE gdelt_state (
            id              INTEGER PRIMARY KEY DEFAULT 1,
            last_batch_ts   TIMESTAMPTZ,
            last_run_at     TIMESTAMPTZ,
            last_events_count   INTEGER DEFAULT 0,
            last_gkg_count      INTEGER DEFAULT 0,
            CONSTRAINT gdelt_state_singleton CHECK (id = 1)
        )
        """
    )
    op.execute("INSERT INTO gdelt_state (id) VALUES (1)")


def downgrade() -> None:
    if not _is_timescale():
        return
    op.execute("DROP TABLE IF EXISTS gdelt_state")
    op.execute("DROP TABLE IF EXISTS gdelt_gkg")
    op.execute("DROP TABLE IF EXISTS gdelt_events")
