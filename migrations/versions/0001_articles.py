"""articles table + indexes

Revision ID: 0001_articles
Revises:
Create Date: 2026-04-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_articles"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        """
        CREATE TABLE articles (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            url               TEXT NOT NULL UNIQUE,
            content_hash      TEXT NOT NULL,
            title             TEXT,
            content           TEXT,
            author            TEXT,
            published_at      TIMESTAMPTZ,
            ingested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source_name       TEXT NOT NULL,
            source_domain     TEXT NOT NULL,
            category          TEXT,
            language          TEXT NOT NULL DEFAULT 'en',
            word_count        INTEGER,
            extraction_method TEXT NOT NULL,
            search_vector     TSVECTOR GENERATED ALWAYS AS (
                to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, ''))
            ) STORED
        )
        """
    )

    op.execute("CREATE INDEX idx_articles_published_at  ON articles (published_at DESC)")
    op.execute("CREATE INDEX idx_articles_ingested_at   ON articles (ingested_at DESC)")
    op.execute("CREATE INDEX idx_articles_source_domain ON articles (source_domain)")
    op.execute("CREATE INDEX idx_articles_category      ON articles (category)")
    op.execute("CREATE INDEX idx_articles_search_vector ON articles USING GIN (search_vector)")
    op.execute("CREATE INDEX idx_articles_content_hash  ON articles (content_hash)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS articles")
