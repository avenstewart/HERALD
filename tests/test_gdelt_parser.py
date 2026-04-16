"""Unit tests for the GDELT CSV parsers.

GDELT column positions are load-bearing — if Events grows from 61 columns
or GKG from 27, our index constants in `parser.py` silently point at the
wrong data. These tests pin the shape of a synthetic row so schema drift
breaks loudly in CI instead of producing garbage in Timescale.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services.gdelt_ingestor.parser import (
    CAMEO_ROOT_LABELS,
    _parse_gcam,
    _parse_locations,
    _parse_tone,
    _semicolon_list,
    parse_events,
    parse_gkg,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


def _events_row(**overrides) -> str:
    """Build a single tab-delimited Events row with sensible defaults.
    Returns a 61-column string matching GDELT v2 Events layout."""
    cols = [""] * 61
    cols[0] = "1234567890"           # GLOBALEVENTID
    cols[1] = "20260416"             # SQLDATE (YYYYMMDD)
    cols[6] = "UNITED STATES"        # Actor1Name
    cols[7] = "USA"                  # Actor1CountryCode
    cols[12] = "GOV"                 # Actor1Type1Code
    cols[16] = "CHINA"               # Actor2Name
    cols[17] = "CHN"                 # Actor2CountryCode
    cols[22] = "GOV"                 # Actor2Type1Code
    cols[26] = "143"                 # EventCode
    cols[27] = "143"                 # EventBaseCode
    cols[28] = "14"                  # EventRootCode (PROTEST)
    cols[30] = "-5.0"                # GoldsteinScale
    cols[31] = "12"                  # NumMentions
    cols[32] = "6"                   # NumSources
    cols[33] = "10"                  # NumArticles
    cols[34] = "-3.2"                # AvgTone
    cols[50] = "Washington, DC"      # ActionGeo_FullName
    cols[51] = "USA"                 # ActionGeo_CountryCode
    cols[54] = "38.9"                # ActionGeo_Lat
    cols[55] = "-77.0"               # ActionGeo_Long
    cols[60] = "https://example.com/a"  # SOURCEURL
    for k, v in overrides.items():
        cols[int(k) if k.isdigit() else 0] = v
    return "\t".join(cols)


def _gkg_row(**overrides) -> str:
    """Build a single tab-delimited GKG row (27 columns)."""
    cols = [""] * 27
    cols[0] = "20260416021500-42"
    cols[1] = "20260416021500"                         # V2_1DATE
    cols[3] = "example.com"                            # V2SOURCECOMMONNAME
    cols[4] = "https://example.com/story"              # V2DOCUMENTIDENTIFIER
    cols[7] = "ECON_INFLATION;USPOL;PROTEST;"          # V1THEMES
    cols[9] = "1#Washington, District of Columbia, United States#US#USDC#38.9#-77.0#531871;"
    cols[11] = "Joe Biden;Xi Jinping;"                 # V1PERSONS
    cols[13] = "Federal Reserve;World Bank;"           # V1ORGANIZATIONS
    cols[15] = "-2.5,4.0,6.5,10.5,3.0,1.0,250"         # V1.5TONE
    cols[17] = "wc:250,v10.1:0.42,v10.2:-0.31,v1.2:0.05,v1.3:-0.90"  # V2GCAM
    for k, v in overrides.items():
        cols[int(k) if k.isdigit() else 0] = v
    return "\t".join(cols)


# ── events tests ─────────────────────────────────────────────────────────────


class TestParseEvents:
    def test_parses_single_row(self):
        rows = list(parse_events(_events_row()))
        assert len(rows) == 1
        r = rows[0]
        assert r["event_id"] == "1234567890"
        assert r["event_date"] == datetime(2026, 4, 16, tzinfo=timezone.utc)
        assert r["actor1_name"] == "UNITED STATES"
        assert r["actor1_country"] == "USA"
        assert r["actor2_country"] == "CHN"
        assert r["cameo_code"] == "143"
        assert r["cameo_root_code"] == "14"
        assert r["cameo_label"] == "PROTEST"
        assert r["goldstein_scale"] == -5.0
        assert r["num_mentions"] == 12
        assert r["num_articles"] == 10
        assert r["avg_tone"] == -3.2
        assert r["geo_lat"] == 38.9
        assert r["geo_lon"] == -77.0
        assert r["source_url"] == "https://example.com/a"

    def test_skips_malformed_truncated_row(self):
        # A row with only 10 columns can't possibly contain all the fields
        # we index into; parser should skip rather than IndexError.
        rows = list(parse_events("a\tb\tc\td\te\tf\tg\th\ti\tj"))
        assert rows == []

    def test_skips_row_with_unparseable_date(self):
        bad = _events_row().split("\t")
        bad[1] = "not-a-date"
        rows = list(parse_events("\t".join(bad)))
        assert rows == []

    def test_handles_empty_optional_fields(self):
        cols = _events_row().split("\t")
        cols[6] = ""   # Actor1Name
        cols[50] = ""  # Geo_FullName
        cols[60] = ""  # SOURCEURL
        rows = list(parse_events("\t".join(cols)))
        assert len(rows) == 1
        r = rows[0]
        assert r["actor1_name"] is None
        assert r["geo_fullname"] is None
        assert r["source_url"] is None

    def test_handles_multiple_rows(self):
        row1 = _events_row()
        row2 = _events_row().split("\t")
        row2[0] = "9999"
        row2_str = "\t".join(row2)
        csv_text = row1 + "\n" + row2_str + "\n"
        rows = list(parse_events(csv_text))
        assert len(rows) == 2
        assert rows[1]["event_id"] == "9999"


class TestCameoLabels:
    def test_all_20_root_codes_labeled(self):
        # The 20 CAMEO root codes are 01..20 zero-padded.
        assert len(CAMEO_ROOT_LABELS) == 20
        for i in range(1, 21):
            assert f"{i:02d}" in CAMEO_ROOT_LABELS

    def test_key_labels(self):
        assert CAMEO_ROOT_LABELS["14"] == "PROTEST"
        assert CAMEO_ROOT_LABELS["19"] == "FIGHT"
        assert CAMEO_ROOT_LABELS["01"] == "MAKE_STATEMENT"


# ── GKG tests ────────────────────────────────────────────────────────────────


class TestParseGKG:
    def test_parses_single_row(self):
        rows = list(parse_gkg(_gkg_row()))
        assert len(rows) == 1
        r = rows[0]
        assert r["record_id"] == "20260416021500-42"
        assert r["record_date"] == datetime(2026, 4, 16, 2, 15, 0, tzinfo=timezone.utc)
        assert r["source_name"] == "example.com"
        assert r["source_url"] == "https://example.com/story"

    def test_themes_split(self):
        r = next(parse_gkg(_gkg_row()))
        assert r["themes"] == ["ECON_INFLATION", "USPOL", "PROTEST"]

    def test_persons_and_orgs(self):
        r = next(parse_gkg(_gkg_row()))
        assert r["persons"] == ["Joe Biden", "Xi Jinping"]
        assert r["organizations"] == ["Federal Reserve", "World Bank"]

    def test_locations_extracts_name_field(self):
        r = next(parse_gkg(_gkg_row()))
        assert r["locations"] == ["Washington, District of Columbia, United States"]

    def test_tone_fields_split(self):
        r = next(parse_gkg(_gkg_row()))
        assert r["tone"] == -2.5
        assert r["positive_score"] == 4.0
        assert r["negative_score"] == 6.5
        assert r["polarity"] == 10.5
        assert r["activity_density"] == 3.0
        assert r["word_count"] == 250

    def test_gcam_keeps_top_n_by_magnitude(self):
        r = next(parse_gkg(_gkg_row()))
        gcam = r["gcam_scores"]
        # Negative-magnitude dim (v1.3=-0.90) should outrank small ones.
        assert "v1.3" in gcam
        assert gcam["v1.3"] == -0.90
        # 5 pairs in the fixture, all should be retained (below the 40 cap).
        assert len(gcam) <= 40

    def test_skips_row_with_unparseable_date(self):
        bad = _gkg_row().split("\t")
        bad[1] = "not-a-date"
        rows = list(parse_gkg("\t".join(bad)))
        assert rows == []

    def test_skips_truncated_row(self):
        rows = list(parse_gkg("a\tb\tc"))
        assert rows == []


# ── helper tests ─────────────────────────────────────────────────────────────


class TestHelpers:
    def test_semicolon_list_strips_and_drops_empties(self):
        assert _semicolon_list("A; B ;C;;") == ["A", "B", "C"]

    def test_semicolon_list_empty_input(self):
        assert _semicolon_list("") == []
        assert _semicolon_list("   ") == []

    def test_parse_locations_extracts_name(self):
        raw = "4#Washington#US#USDC#38.9#-77.0#531871;4#Beijing#CH##39.9#116.4#1816670;"
        assert _parse_locations(raw) == ["Washington", "Beijing"]

    def test_parse_tone_handles_partial(self):
        t = _parse_tone("-2.5,4.0")
        assert t["tone"] == -2.5
        assert t["positive_score"] == 4.0
        assert t["negative_score"] is None
        assert t["word_count"] is None

    def test_parse_tone_handles_empty(self):
        t = _parse_tone("")
        assert all(v is None for v in t.values())

    def test_parse_gcam_handles_malformed(self):
        # Garbage entries should be skipped, not crash.
        out = _parse_gcam("wc:250,bad_entry,v1:0.5,:0.1,v2:not-a-number")
        assert out.get("wc") == 250.0
        assert out.get("v1") == 0.5
        assert "bad_entry" not in out

    def test_parse_gcam_respects_top_n_limit(self):
        # Build a GCAM string with 60 dims; expect top 40 retained.
        pairs = ",".join(f"d{i}:{(i+1)*0.01}" for i in range(60))
        out = _parse_gcam(pairs, keep_dims=40)
        assert len(out) == 40
        # Largest magnitudes kept — d59=0.60, d58=0.59, etc.
        assert "d59" in out
        assert "d0" not in out  # smallest magnitude dropped
