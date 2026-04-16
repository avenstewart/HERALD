"""Parse GDELT v2 Events and GKG CSV streams.

Both files are tab-delimited, with nested fields inside cells separated by
semicolons (list items) and hash marks or pipes (substructure delimiters).
We normalize to flat dicts that match the columns in migration 0002.

References:
  Events codebook: http://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf
  GKG codebook:    http://data.gdeltproject.org/documentation/GDELT-Global_Knowledge_Graph_Codebook-V2.1.pdf
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime, timezone
from io import StringIO
from typing import Any


# ── helpers ──────────────────────────────────────────────────────────────────


def _parse_sqldate(value: str) -> datetime | None:
    """Events uses SQLDATE = YYYYMMDD (integer)."""
    if not value or not value.strip():
        return None
    v = value.strip()
    try:
        return datetime.strptime(v, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_gkg_date(value: str) -> datetime | None:
    """GKG V2.1DATE = YYYYMMDDHHMMSS."""
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _float_or_none(v: str) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int_or_none(v: str) -> int | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def _semicolon_list(value: str) -> list[str]:
    """Split `A;B;C;` → ['A','B','C']. Handles trailing semicolons and
    stripped whitespace; empty items removed."""
    if not value:
        return []
    items = [x.strip() for x in value.split(";")]
    return [x for x in items if x]


# ── CAMEO root codes (most common) — used to label events at ingest time ────
# Full codebook has ~300 codes; we label the 20 root codes which are the
# coarse-grained categories. Leaf codes remain in `cameo_code`.
CAMEO_ROOT_LABELS: dict[str, str] = {
    "01": "MAKE_STATEMENT",
    "02": "APPEAL",
    "03": "EXPRESS_INTENT_TO_COOPERATE",
    "04": "CONSULT",
    "05": "ENGAGE_IN_DIPLOMATIC_COOPERATION",
    "06": "ENGAGE_IN_MATERIAL_COOPERATION",
    "07": "PROVIDE_AID",
    "08": "YIELD",
    "09": "INVESTIGATE",
    "10": "DEMAND",
    "11": "DISAPPROVE",
    "12": "REJECT",
    "13": "THREATEN",
    "14": "PROTEST",
    "15": "EXHIBIT_FORCE_POSTURE",
    "16": "REDUCE_RELATIONS",
    "17": "COERCE",
    "18": "ASSAULT",
    "19": "FIGHT",
    "20": "USE_UNCONVENTIONAL_MASS_VIOLENCE",
}


# ── Events parser ────────────────────────────────────────────────────────────

# GDELT Events has 61 columns in this fixed order (see codebook). We only use
# a subset but still have to read all 61 to index correctly. Column indices:
_EV = {
    "GLOBALEVENTID": 0,
    "SQLDATE": 1,
    "Actor1Name": 6,
    "Actor1CountryCode": 7,
    "Actor1Type1Code": 12,
    "Actor2Name": 16,
    "Actor2CountryCode": 17,
    "Actor2Type1Code": 22,
    "EventCode": 26,
    "EventBaseCode": 27,
    "EventRootCode": 28,
    "GoldsteinScale": 30,
    "NumMentions": 31,
    "NumSources": 32,
    "NumArticles": 33,
    "AvgTone": 34,
    "ActionGeo_FullName": 50,
    "ActionGeo_CountryCode": 51,
    "ActionGeo_Lat": 54,
    "ActionGeo_Long": 55,
    "SOURCEURL": 60,
}


def parse_events(csv_text: str) -> Iterator[dict[str, Any]]:
    """Yield normalized event dicts from the tab-delimited Events CSV."""
    reader = csv.reader(StringIO(csv_text), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if len(row) < 61:
            # Truncated / malformed row — skip.
            continue
        event_date = _parse_sqldate(row[_EV["SQLDATE"]])
        if event_date is None:
            continue
        root = (row[_EV["EventRootCode"]] or "").strip()
        yield {
            "event_id": row[_EV["GLOBALEVENTID"]].strip(),
            "event_date": event_date,
            "actor1_name": row[_EV["Actor1Name"]].strip() or None,
            "actor1_country": row[_EV["Actor1CountryCode"]].strip() or None,
            "actor1_type": row[_EV["Actor1Type1Code"]].strip() or None,
            "actor2_name": row[_EV["Actor2Name"]].strip() or None,
            "actor2_country": row[_EV["Actor2CountryCode"]].strip() or None,
            "actor2_type": row[_EV["Actor2Type1Code"]].strip() or None,
            "cameo_code": row[_EV["EventCode"]].strip() or row[_EV["EventBaseCode"]].strip(),
            "cameo_root_code": root,
            "cameo_label": CAMEO_ROOT_LABELS.get(root),
            "goldstein_scale": _float_or_none(row[_EV["GoldsteinScale"]]),
            "num_mentions": _int_or_none(row[_EV["NumMentions"]]),
            "num_sources": _int_or_none(row[_EV["NumSources"]]),
            "num_articles": _int_or_none(row[_EV["NumArticles"]]),
            "avg_tone": _float_or_none(row[_EV["AvgTone"]]),
            "geo_fullname": row[_EV["ActionGeo_FullName"]].strip() or None,
            "geo_country": row[_EV["ActionGeo_CountryCode"]].strip() or None,
            "geo_lat": _float_or_none(row[_EV["ActionGeo_Lat"]]),
            "geo_lon": _float_or_none(row[_EV["ActionGeo_Long"]]),
            "source_url": row[_EV["SOURCEURL"]].strip() or None,
        }


# ── GKG parser ───────────────────────────────────────────────────────────────

# GKG has 27 columns. The ones we need:
_GK = {
    "GKGRECORDID": 0,
    "V2_1DATE": 1,
    "V2SOURCECOMMONNAME": 3,
    "V2DOCUMENTIDENTIFIER": 4,
    "V1THEMES": 7,
    "V1LOCATIONS": 9,
    "V1PERSONS": 11,
    "V1ORGANIZATIONS": 13,
    "V1_5TONE": 15,
    "V2GCAM": 17,
}


def _parse_locations(raw: str) -> list[str]:
    """Each location block is 'type#name#cc#adm1#lat#lon#featureid';
    we keep just the name."""
    out: list[str] = []
    for block in _semicolon_list(raw):
        parts = block.split("#")
        if len(parts) >= 2 and parts[1]:
            out.append(parts[1].strip())
    return out


def _parse_tone(raw: str) -> dict[str, float | int | None]:
    """V1.5TONE = tone,positive,negative,polarity,activityRef,selfGroupRef,wordCount."""
    parts = [p.strip() for p in (raw or "").split(",")]
    parts += [""] * max(0, 7 - len(parts))
    return {
        "tone": _float_or_none(parts[0]),
        "positive_score": _float_or_none(parts[1]),
        "negative_score": _float_or_none(parts[2]),
        "polarity": _float_or_none(parts[3]),
        "activity_density": _float_or_none(parts[4]),
        "word_count": _int_or_none(parts[6]),
    }


def _parse_gcam(raw: str, keep_dims: int = 40) -> dict[str, float]:
    """V2GCAM is semicolon-separated 'dim:value' pairs. GDELT exposes ~2300
    dimensions; we keep only the strongest-magnitude ones to bound the JSONB
    size per row.

    For v1.0 we do NOT store the full GCAM (it's ~15 KB/row * 1M rows/day).
    Selective indexing over these stored dims stays the caller's job.
    """
    if not raw:
        return {}
    pairs: list[tuple[str, float]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        dim, _, v = chunk.partition(":")
        try:
            pairs.append((dim.strip(), float(v)))
        except ValueError:
            continue
    # Keep the top-N by absolute value.
    pairs.sort(key=lambda x: abs(x[1]), reverse=True)
    return {k: v for k, v in pairs[:keep_dims]}


def parse_gkg(csv_text: str) -> Iterator[dict[str, Any]]:
    reader = csv.reader(StringIO(csv_text), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if len(row) < 18:
            continue
        rec_date = _parse_gkg_date(row[_GK["V2_1DATE"]])
        if rec_date is None:
            continue
        tone = _parse_tone(row[_GK["V1_5TONE"]])
        yield {
            "record_id": row[_GK["GKGRECORDID"]].strip(),
            "record_date": rec_date,
            "source_url": row[_GK["V2DOCUMENTIDENTIFIER"]].strip() or "",
            "source_name": row[_GK["V2SOURCECOMMONNAME"]].strip() or None,
            "themes": _semicolon_list(row[_GK["V1THEMES"]]),
            "locations": _parse_locations(row[_GK["V1LOCATIONS"]]),
            "persons": _semicolon_list(row[_GK["V1PERSONS"]]),
            "organizations": _semicolon_list(row[_GK["V1ORGANIZATIONS"]]),
            "tone": tone["tone"],
            "positive_score": tone["positive_score"],
            "negative_score": tone["negative_score"],
            "polarity": tone["polarity"],
            "activity_density": tone["activity_density"],
            "word_count": tone["word_count"],
            "gcam_scores": _parse_gcam(row[_GK["V2GCAM"]]),
        }
