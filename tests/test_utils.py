from datetime import timezone

import pytest

from shared.utils import content_hash, domain_of, normalize_url, parse_date, sha256_hex


class TestNormalizeUrl:
    def test_lowercases_scheme_and_host(self):
        assert normalize_url("HTTPS://Example.COM/PATH") == "https://example.com/PATH"

    def test_strips_default_ports(self):
        assert normalize_url("http://example.com:80/x") == "http://example.com/x"
        assert normalize_url("https://example.com:443/x") == "https://example.com/x"

    def test_keeps_nonstandard_port(self):
        assert normalize_url("http://example.com:8080/x") == "http://example.com:8080/x"

    def test_strips_fragment(self):
        assert normalize_url("https://example.com/x#section") == "https://example.com/x"

    def test_strips_utm_params(self):
        assert (
            normalize_url("https://example.com/x?utm_source=foo&utm_campaign=bar&id=42")
            == "https://example.com/x?id=42"
        )

    def test_strips_fbclid_gclid(self):
        assert (
            normalize_url("https://example.com/x?fbclid=abc&gclid=def&kept=yes")
            == "https://example.com/x?kept=yes"
        )

    def test_collapses_trailing_slash(self):
        assert normalize_url("https://example.com/path/") == "https://example.com/path"

    def test_preserves_root_slash(self):
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_empty_string(self):
        assert normalize_url("") == ""


class TestHashing:
    def test_sha256_stable(self):
        assert sha256_hex("hello") == sha256_hex("hello")
        assert len(sha256_hex("x")) == 64

    def test_content_hash_stable(self):
        a = content_hash("Title", "Body content here")
        b = content_hash("Title", "Body content here")
        assert a == b

    def test_content_hash_changes_with_title(self):
        a = content_hash("Title A", "Same body")
        b = content_hash("Title B", "Same body")
        assert a != b

    def test_content_hash_uses_only_first_500_chars(self):
        body1 = "x" * 500 + "y" * 1000
        body2 = "x" * 500 + "z" * 1000
        assert content_hash("t", body1) == content_hash("t", body2)

    def test_content_hash_handles_none(self):
        assert content_hash(None, None) == content_hash("", "")


class TestParseDate:
    def test_iso8601(self):
        d = parse_date("2026-04-15T12:00:00Z")
        assert d is not None
        assert d.tzinfo is not None

    def test_naive_becomes_utc(self):
        d = parse_date("2026-04-15 12:00:00")
        assert d.tzinfo is timezone.utc

    def test_none_passthrough(self):
        assert parse_date(None) is None

    def test_garbage_returns_none(self):
        assert parse_date("not a date") is None

    def test_empty_returns_none(self):
        assert parse_date("") is None


class TestDomainOf:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.bbc.co.uk/news", "bbc.co.uk"),
            ("https://example.com:8080/x", "example.com"),
            ("https://EXAMPLE.com/", "example.com"),
            ("not a url", ""),
        ],
    )
    def test_cases(self, url, expected):
        assert domain_of(url) == expected
