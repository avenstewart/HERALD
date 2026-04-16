"""SQLAlchemy 2.x models.

Two separate metadata objects so Alembic can target the correct database:
  - `articles_metadata` → Postgres (the default articles DB)
  - `gdelt_metadata`    → TimescaleDB

Migrations that touch a given DB should only reference tables from that DB's
metadata object.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    JSON,
    Column,
    Computed,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ── metadata namespaces ──────────────────────────────────────────────────────

articles_metadata = MetaData()
gdelt_metadata = MetaData()


class ArticlesBase(DeclarativeBase):
    metadata = articles_metadata


class GdeltBase(DeclarativeBase):
    metadata = gdelt_metadata


# ── articles (Postgres) ──────────────────────────────────────────────────────


class Article(ArticlesBase):
    __tablename__ = "articles"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default="en")
    word_count: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[str] = mapped_column(Text, nullable=False)

    search_vector = Column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, ''))",
            persisted=True,
        ),
    )

    __table_args__ = (
        Index("idx_articles_published_at", "published_at", postgresql_ops={"published_at": "DESC"}),
        Index("idx_articles_ingested_at", "ingested_at", postgresql_ops={"ingested_at": "DESC"}),
        Index("idx_articles_source_domain", "source_domain"),
        Index("idx_articles_category", "category"),
        Index("idx_articles_search_vector", "search_vector", postgresql_using="gin"),
        Index("idx_articles_content_hash", "content_hash"),
    )


# ── gdelt tables (TimescaleDB) — schema shells; hypertable + retention are ──
# ── applied in migration 0002 which is delivered in a later release.      ──


class GdeltEvent(GdeltBase):
    __tablename__ = "gdelt_events"

    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor1_name: Mapped[str | None] = mapped_column(Text)
    actor1_country: Mapped[str | None] = mapped_column(Text)
    actor1_type: Mapped[str | None] = mapped_column(Text)
    actor2_name: Mapped[str | None] = mapped_column(Text)
    actor2_country: Mapped[str | None] = mapped_column(Text)
    actor2_type: Mapped[str | None] = mapped_column(Text)
    cameo_code: Mapped[str] = mapped_column(Text, nullable=False)
    cameo_root_code: Mapped[str] = mapped_column(Text, nullable=False)
    cameo_label: Mapped[str | None] = mapped_column(Text)
    goldstein_scale: Mapped[float | None] = mapped_column(Float)
    num_mentions: Mapped[int | None] = mapped_column(Integer)
    num_sources: Mapped[int | None] = mapped_column(Integer)
    num_articles: Mapped[int | None] = mapped_column(Integer)
    avg_tone: Mapped[float | None] = mapped_column(Float)
    geo_fullname: Mapped[str | None] = mapped_column(Text)
    geo_country: Mapped[str | None] = mapped_column(Text)
    geo_lat: Mapped[float | None] = mapped_column(Float)
    geo_lon: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (PrimaryKeyConstraint("event_id", "event_date"),)


class GdeltGKG(GdeltBase):
    __tablename__ = "gdelt_gkg"

    record_id: Mapped[str] = mapped_column(Text, nullable=False)
    record_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(Text)
    themes = Column(ARRAY(Text))
    locations = Column(ARRAY(Text))
    persons = Column(ARRAY(Text))
    organizations = Column(ARRAY(Text))
    tone: Mapped[float | None] = mapped_column(Float)
    positive_score: Mapped[float | None] = mapped_column(Float)
    negative_score: Mapped[float | None] = mapped_column(Float)
    polarity: Mapped[float | None] = mapped_column(Float)
    activity_density: Mapped[float | None] = mapped_column(Float)
    word_count: Mapped[int | None] = mapped_column(Integer)
    gcam_scores = Column(JSONB)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (PrimaryKeyConstraint("record_id", "record_date"),)


__all__ = [
    "articles_metadata",
    "gdelt_metadata",
    "ArticlesBase",
    "GdeltBase",
    "Article",
    "GdeltEvent",
    "GdeltGKG",
    "JSON",
]
