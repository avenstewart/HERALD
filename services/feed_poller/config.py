"""Load and validate sources/feeds.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

Tier = Literal["A", "B", "C"]
TIER_INTERVALS: dict[Tier, int] = {"A": 120, "B": 300, "C": 900}


class Source(BaseModel):
    name: str
    tier: Tier
    category: str = "general"
    url: str | None = None
    rsshub_route: str | None = Field(default=None, description="Path into the RSSHub service")

    @field_validator("rsshub_route")
    @classmethod
    def _normalize_route(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return v if v.startswith("/") else f"/{v}"

    def resolve_url(self, rsshub_base: str) -> str:
        if self.url:
            return self.url
        if self.rsshub_route:
            base = rsshub_base.rstrip("/")
            return f"{base}{self.rsshub_route}"
        raise ValueError(f"Source {self.name!r} has neither `url` nor `rsshub_route`")

    @property
    def interval_seconds(self) -> int:
        return TIER_INTERVALS[self.tier]


class SourceCatalogue(BaseModel):
    sources: list[Source]


def load_sources(path: Path) -> list[Source]:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return SourceCatalogue.model_validate(raw).sources
