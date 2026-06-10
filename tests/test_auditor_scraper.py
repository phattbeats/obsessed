"""
Tests for the county auditor / property-records scraper (PHA-798).

These tests focus on the improvements over the original implementation:
  • Markdown-table record parsing (new layout)
  • Two-column whitespace-separated record parsing
  • Expanded field set: sale price, deed date, last sale date, year built,
    owner history, school district (in addition to the originals)
  • TPAD address normalization ("STREET NAME  NUMBER" → "NUMBER STREET NAME")
  • TPAD retry-on-transient-error behavior
  • Multi-source fallback chain (crawl4ai → FlareSolverr) — sources tried
    in order; the first one that returns a non-sentinel response wins
  • Backwards compatibility: OH_AUDITOR_URLS and AUDITOR_SEARCH_URLS aliases
    must still expose the original county list

Network / live calls are skipped by default — they require crawl4ai and
FlareSolverr to be reachable. Enable with AUDITOR_LIVE_TESTS=1.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.services.scraper import auditor
from app.services.scraper.auditor import (
    AUDITOR_SEARCH_URLS,
    COUNTY_FALLBACK_URLS,
    OH_AUDITOR_URLS,
    STATE_PORTALS,
    TN_COUNTY_CODES,
    TN_EXTERNAL_COUNTIES,
    _FIELD_ALIASES,
    _match_field,
    _normalise_key,
    _normalise_tpad_address,
    _scrape_auditor_url_with_fallback,
    _scrape_via_crawl4ai,
    _scrape_via_flaresolverr,
    parse_property_record,
    search_property_records,
    get_property_by_address,
    get_property_details,
)


# --- Backwards-compat aliases -------------------------------------------


def test_oh_auditor_urls_alias_exposes_original_county_keys():
    """Original import name must still resolve to a dict with all the
    original central-Ohio counties as keys."""
    assert isinstance(OH_AUDITOR_URLS, dict)
    # All original county keys from the pre-PHA-798 map must still resolve
    for county in (
        "franklin", "delaware", "licking", "fairfield", "union", "madison",
        "pickaway", "hocking", "athens", "vinton",
    ):
        assert county in OH_AUDITOR_URLS, f"missing legacy county: {county}"
        # The legacy alias stores the base URL (not a tuple)
        assert OH_AUDITOR_URLS[county].startswith("http")


def test_auditor_search_urls_alias_points_to_oh_map():
    """The other legacy alias must point to the same map as OH_AUDITOR_URLS."""
    assert AUDITOR_SEARCH_URLS is OH_AUDITOR_URLS


def test_county_fallback_urls_contains_expanded_coverage():
    """PHA-798 expanded the fallback map to cover ~70% of OH population.
    Spot-check a few of the new high-population counties."""
    for county in (
        "cuyahoga", "hamilton", "summit", "montgomery", "lucas", "stark",
        "butler", "warren", "clermont", "lorain",
    ):
        assert county in COUNTY_FALLBACK_URLS, f"missing expanded county: {county}"
        base_url, hints = COUNTY_FALLBACK_URLS[county]
        assert base_url.startswith("http")
        assert isinstance(hints, list) and hints, f"{county} has no path hints"


# --- Field alias coverage ----------------------------------------------


def test_field_aliases_cover_new_and_legacy_fields():
    """All the new PHA-798 fields must be in the alias map."""
    for canonical in (
        "owner", "address", "parcel_id", "acreage", "market_value",
        "taxable_value", "sale_price", "deed_date", "last_sale_date",
        "year_built", "owner_history", "school_district",
    ):
        assert canonical in _FIELD_ALIASES
        assert len(_FIELD_ALIASES[canonical]) >= 1


# --- _normalise_key / _match_field -------------------------------------


def test_normalise_key_strips_colon_and_lowercases():
    assert _normalise_key("Owner:") == "owner"
    assert _normalise_key("  SALE PRICE  ") == "sale price"
    assert _normalise_key("Year Built:\t1980") == "year built 1980"


def test_match_field_handles_colon_layout():
    assert _match_field("Owner: John Smith") == "owner"
    assert _match_field("Sale Price: $250,000") == "sale_price"
    assert _match_field("Deed Date: 2018-04-12") == "deed_date"
    assert _match_field("Year Built: 1980") == "year_built"
    assert _match_field("Owner History: Smith → Jones") == "owner_history"


def test_match_field_handles_markdown_table_layout():
    assert _match_field("| Owner | John Smith |") == "owner"
    assert _match_field("| Sale Price | $250,000 |") == "sale_price"
    assert _match_field("| School District | Hilliard |") == "school_district"


def test_match_field_handles_whitespace_separated_layout():
    # Auditor sites sometimes render as "Field   Value" with big gaps
    assert _match_field("Owner        John Smith") == "owner"
    assert _match_field("Market Value  $425,000") == "market_value"


def test_match_field_returns_none_for_unknown():
    assert _match_field("Random text on a line") is None
    assert _match_field("") is None


# --- parse_property_record: layout handling ---------------------------


def test_parse_property_record_field_colon_layout():
    raw = """
    Owner: Aaron Tom
    Address: 123 Main St
    Parcel ID: 123-45-678
    Acreage: 0.5
    Market Value: $425,000
    Taxable Value: $300,000
    Sale Price: $400,000
    Deed Date: 2018-04-12
    Last Sale Date: 2018-04-15
    Year Built: 1998
    School District: Hilliard
    Owner History: Smith → Jones → Tom
    """
    rec = parse_property_record(raw, search_term="Aaron Tom")
    assert rec["owner"] == "Aaron Tom"
    assert rec["address"] == "123 Main St"
    assert rec["parcel_id"] == "123-45-678"
    assert rec["acreage"] == "0.5"
    assert rec["market_value"] == "$425,000"
    assert rec["taxable_value"] == "$300,000"
    assert rec["sale_price"] == "$400,000"
    assert rec["deed_date"] == "2018-04-12"
    assert rec["last_sale_date"] == "2018-04-15"
    assert rec["year_built"] == "1998"
    assert rec["school_district"] == "Hilliard"
    assert rec["owner_history"] == "Smith → Jones → Tom"
    assert rec["search_term"] == "Aaron Tom"


def test_parse_property_record_markdown_table_layout():
    raw = """
    | Owner | Jane Doe |
    | Address | 456 Oak Ave |
    | Parcel | 987-65-432 |
    | Market Value | $510,000 |
    | Sale Price | $495,000 |
    | Year Built | 2005 |
    | School District | Worthington |
    """
    rec = parse_property_record(raw, search_term="Jane Doe")
    assert rec["owner"] == "Jane Doe"
    assert rec["address"] == "456 Oak Ave"
    assert rec["parcel_id"] == "987-65-432"
    assert rec["market_value"] == "$510,000"
    assert rec["sale_price"] == "$495,000"
    assert rec["year_built"] == "2005"
    assert rec["school_district"] == "Worthington"


def test_parse_property_record_whitespace_separated_layout():
    # Whitespace-separated layout uses 2+ spaces between field and value.
    # This is the "two-column list" layout some auditor sites render.
    raw = """
    Owner        Aaron Tom
    Address      123 Main St
    Market Value        $425,000
    """
    rec = parse_property_record(raw, search_term="Aaron Tom")
    assert rec["owner"] == "Aaron Tom"
    assert rec["address"] == "123 Main St"
    assert rec["market_value"] == "$425,000"


def test_parse_property_record_preserves_currency_formatting():
    """Don't strip the dollar sign or commas — trivia callers expect them."""
    raw = "Sale Price: $1,250,000"
    rec = parse_property_record(raw, search_term="x")
    assert rec["sale_price"] == "$1,250,000"


def test_parse_property_record_empty_record_has_all_keys():
    """Empty input must still return a dict with all the new keys initialised
    to '' — callers rely on the shape."""
    rec = parse_property_record("", search_term="x")
    for canonical in _FIELD_ALIASES:
        assert canonical in rec
        assert rec[canonical] == ""
    assert rec["search_term"] == "x"


def test_parse_property_record_uses_first_match_per_field():
    """If the same field appears twice, the first non-empty value wins.
    Mirrors the original "if not record[canonical]" guard."""
    raw = """
    Owner: First Name
    Owner: Second Name
    """
    rec = parse_property_record(raw, search_term="x")
    assert rec["owner"] == "First Name"


# --- TPAD address normalization ----------------------------------------


def test_normalise_tpad_address_swaps_name_and_number():
    assert _normalise_tpad_address("FORT HENRY DR  1797") == "1797 FORT HENRY DR"
    assert _normalise_tpad_address("OAK ST  201") == "201 OAK ST"


def test_normalise_tpad_address_passthrough():
    # Already in conventional form — leave alone
    assert _normalise_tpad_address("1797 Fort Henry Dr") == "1797 Fort Henry Dr"
    # No trailing number — leave alone
    assert _normalise_tpad_address("FORT HENRY DR") == "FORT HENRY DR"
    # Empty
    assert _normalise_tpad_address("") == ""


# --- TN registry shape -------------------------------------------------


def test_state_portals_has_tennessee():
    cfg = STATE_PORTALS.get("tennessee")
    assert cfg is not None
    assert cfg["portal_type"] == "tpad"
    assert cfg["base_url"].startswith("https://")
    assert "anderson" in cfg["county_codes"]  # spot-check
    assert "shelby" in cfg["external_counties"]  # Memphis, external


def test_tn_external_counties_list_is_nonempty():
    """External counties (davidson, shelby, etc.) must be flagged so the
    scraper knows to fall through to web-search discovery."""
    assert "shelby" in TN_EXTERNAL_COUNTIES
    assert "davidson" in TN_EXTERNAL_COUNTIES
    assert "knox" in TN_EXTERNAL_COUNTIES
    # And they should NOT be in the TPAD county-code map
    assert "shelby" not in TN_COUNTY_CODES
    assert "davidson" not in TN_COUNTY_CODES


# --- _scrape_via_flaresolverr: Cloudflare wall is captured as sentinel -


@pytest.mark.asyncio
async def test_flaresolverr_adapter_captures_cloudflare_wall():
    from app.services.scraper.flaresolverr import CloudflareWallError

    async def _raise(url):
        raise CloudflareWallError("still walled", 0)

    with patch("app.services.scraper.auditor.fs_get", _raise):
        text, meta = await _scrape_via_flaresolverr("https://x.example/")
    assert text.startswith("[flaresolverr:")
    assert "cloudflare" in text.lower()
    assert meta.get("status") == 0


@pytest.mark.asyncio
async def test_flaresolverr_adapter_captures_http_error_status():
    async def _bad(url):
        return ("", 503)

    with patch("app.services.scraper.auditor.fs_get", _bad):
        text, meta = await _scrape_via_flaresolverr("https://x.example/")
    assert text.startswith("[flaresolverr:")
    assert "503" in text
    assert meta.get("status") == 503


@pytest.mark.asyncio
async def test_flaresolverr_adapter_detects_challenge_signature_in_html():
    html = "<html><body>Checking your browser before accessing the site.</body></html>"

    async def _fake(url):
        return (html, 200)

    with patch("app.services.scraper.auditor.fs_get", _fake):
        text, meta = await _scrape_via_flaresolverr("https://x.example/")
    assert text.startswith("[flaresolverr:")
    assert "challenge" in text.lower()


@pytest.mark.asyncio
async def test_flaresolverr_adapter_returns_clean_html():
    html = "<html><body>Property records here</body></html>"

    async def _fake(url):
        return (html, 200)

    with patch("app.services.scraper.auditor.fs_get", _fake):
        text, meta = await _scrape_via_flaresolverr("https://x.example/")
    assert text == html
    assert meta.get("status") == 200


# --- crawl4ai adapter: challenge sentinels -----------------------------


@pytest.mark.asyncio
async def test_crawl4ai_adapter_detects_challenge_page():
    fake = "Checking your browser... DDoS protection by Cloudflare"

    async def _fake(url):
        return (fake, {"title": "x"})

    with patch("app.services.scraper.auditor.crawl4ai_scrape", _fake):
        text, meta = await _scrape_via_crawl4ai("https://x.example/")
    assert text.startswith("[crawl4ai:")
    assert "challenge" in text.lower()


@pytest.mark.asyncio
async def test_crawl4ai_adapter_returns_text_on_success():
    async def _fake(url):
        return ("Real page text", {"title": "Auditor"})

    with patch("app.services.scraper.auditor.crawl4ai_scrape", _fake):
        text, meta = await _scrape_via_crawl4ai("https://x.example/")
    assert text == "Real page text"


# --- Fallback chain: crawl4ai first, FlareSolverr on failure ----------


@pytest.mark.asyncio
async def test_fallback_uses_crawl4ai_first_when_successful():
    calls: list[str] = []

    async def _ok_crawl4ai(url):
        calls.append("crawl4ai")
        return ("good text", {"title": "ok"})

    async def _should_not_call(url):
        calls.append("flaresolverr")
        return ("fs text", {"status": 200})

    with patch("app.services.scraper.auditor._scrape_via_crawl4ai", _ok_crawl4ai), \
         patch("app.services.scraper.auditor._scrape_via_flaresolverr", _should_not_call):
        text, source = await _scrape_auditor_url_with_fallback("https://x.example/")

    assert text == "good text"
    assert source == "crawl4ai"
    assert "flaresolverr" not in calls, "FS should not be called when crawl4ai succeeds on first try"
    assert calls.count("crawl4ai") == 1


@pytest.mark.asyncio
async def test_fallback_retries_crawl4ai_before_dropping_to_flaresolverr():
    """When crawl4ai keeps returning sentinels, the chain must retry that
    source up to 2 times before falling through to FlareSolverr.  FS
    should then be tried."""
    calls: list[str] = []

    async def _fail_crawl4ai(url):
        calls.append("crawl4ai")
        return ("[crawl4ai: cloudflare challenge]", {"title": ""})

    async def _ok_fs(url):
        calls.append("flaresolverr")
        return ("FS HTML", {"status": 200})

    with patch("app.services.scraper.auditor._scrape_via_crawl4ai", _fail_crawl4ai), \
         patch("app.services.scraper.auditor._scrape_via_flaresolverr", _ok_fs):
        text, source = await _scrape_auditor_url_with_fallback("https://x.example/")

    assert text == "FS HTML"
    assert source == "flaresolverr"
    # crawl4ai retried 2x before FS, then FS succeeded on attempt 1
    assert calls == ["crawl4ai", "crawl4ai", "flaresolverr"]


@pytest.mark.asyncio
async def test_fallback_returns_empty_when_all_sources_fail():
    async def _fail_crawl4ai(url):
        return ("[crawl4ai: cloudflare challenge]", {})

    async def _fail_fs(url):
        return ("[flaresolverr: status 403]", {"status": 403})

    with patch("app.services.scraper.auditor._scrape_via_crawl4ai", _fail_crawl4ai), \
         patch("app.services.scraper.auditor._scrape_via_flaresolverr", _fail_fs):
        text, source = await _scrape_auditor_url_with_fallback("https://x.example/")

    assert text == ""
    # All sources exhausted — source label is the sentinel used by the
    # implementation to signal "no source produced usable content"
    assert source in ("unknown", "none")


@pytest.mark.asyncio
async def test_fallback_skips_flaresolverr_when_disabled():
    calls: list[str] = []

    async def _ok_crawl4ai(url):
        calls.append("crawl4ai")
        return ("crawl4ai text", {})

    async def _should_not_call(url):
        calls.append("flaresolverr")
        return ("fs text", {"status": 200})

    with patch("app.services.scraper.auditor._scrape_via_crawl4ai", _ok_crawl4ai), \
         patch("app.services.scraper.auditor._scrape_via_flaresolverr", _should_not_call):
        text, source = await _scrape_auditor_url_with_fallback(
            "https://x.example/", use_flaresolverr=False
        )

    assert "flaresolverr" not in calls, "FS should not be called when disabled"


# --- search_property_records dispatch ----------------------------------


@pytest.mark.asyncio
async def test_search_returns_empty_for_unknown_ohio_county_without_search():
    """For an Ohio county NOT in COUNTY_FALLBACK_URLS, and with web-search
    discovery disabled, the result must be an empty list (not an exception)."""
    with patch("app.services.scraper.auditor.find_auditor_url", AsyncMock(return_value=None)):
        results = await search_property_records("nonexistent_county_xyz", "John Doe", "owner", "Ohio")
    assert results == []


@pytest.mark.asyncio
async def test_search_uses_fallback_url_when_search_returns_none():
    """When web-search fails but the county IS in the fallback map, the
    scraper must use the fallback URL — not return empty."""
    fake_text = "Owner: John Doe\nAddress: 1 Test St\n"

    async def _fake_fallback(url, **kwargs):
        return fake_text, "crawl4ai"

    with patch("app.services.scraper.auditor.find_auditor_url", AsyncMock(return_value=None)), \
         patch("app.services.scraper.auditor._scrape_auditor_url_with_fallback", _fake_fallback):
        results = await search_property_records("franklin", "John Doe", "owner", "Ohio")
    assert isinstance(results, list)
    assert results, "expected at least one record from the fallback map"
    # The matched block must contain the search term
    assert any("John Doe" in r.get("owner", "") for r in results)


@pytest.mark.asyncio
async def test_search_returns_empty_when_scrape_chain_raises():
    """The search call must not propagate exceptions from the scrape chain."""
    async def _explode(url, **kwargs):
        raise RuntimeError("boom")

    with patch("app.services.scraper.auditor.find_auditor_url", AsyncMock(return_value=None)), \
         patch("app.services.scraper.auditor._scrape_auditor_url_with_fallback", _explode):
        results = await search_property_records("franklin", "x", "owner", "Ohio")
    assert results == []


@pytest.mark.asyncio
async def test_search_tpad_uses_state_portal_for_known_county():
    """For a known TN county, the TPAD scraper must be used and the result
    must carry `source='tpad'`."""
    from app.services.scraper.auditor import _search_tpad_tn
    from app.services.scraper.auditor import STATE_PORTALS

    cfg = STATE_PORTALS["tennessee"]

    async def _fake_tpad(county, search_term, portal_cfg):
        return [{
            "owner": "AARON TOM",
            "address": "123 MAIN ST",
            "parcel_id": "001-001",
            "acreage": "",
            "market_value": "",
            "taxable_value": "",
            "sale_price": "",
            "deed_date": "",
            "last_sale_date": "",
            "year_built": "",
            "owner_history": "",
            "school_district": "",
            "search_term": search_term,
            "county": county,
            "source_url": "https://assessment.cot.tn.gov/TPAD/Parcel/Details",
            "source": "tpad",
            "state": "Tennessee",
        }]

    with patch.object(auditor, "_search_tpad_tn", _fake_tpad):
        results = await search_property_records(
            "anderson", "Aaron Tom", "owner", "Tennessee", use_flaresolverr=False
        )
    assert results, "expected TPAD result for known county"
    assert results[0]["source"] == "tpad"
    assert results[0]["owner"] == "AARON TOM"


@pytest.mark.asyncio
async def test_search_tpad_falls_through_to_search_for_external_county():
    """For a TN external county (e.g. Davidson/Nashville), TPAD returns [] —
    the scraper should fall through to web-search discovery."""
    from app.services.scraper.auditor import _search_tpad_tn

    async def _tpad_returns_empty(county, search_term, portal_cfg):
        return []

    async def _fake_fallback(url, **kwargs):
        # Provide a block that contains the search term so we get a record
        return "Owner: AARON TOM\nAddress: 123 MAIN ST\n", "crawl4ai"

    with patch.object(auditor, "_search_tpad_tn", _tpad_returns_empty), \
         patch("app.services.scraper.auditor.find_auditor_url", AsyncMock(return_value="https://davidson-tn.gov/auditor")), \
         patch("app.services.scraper.auditor._scrape_auditor_url_with_fallback", _fake_fallback):
        results = await search_property_records("davidson", "Aaron Tom", "owner", "Tennessee")
    assert isinstance(results, list)
    # External county falls through to web-search, so it goes through
    # the find_auditor_url path which we mocked to return a URL, then
    # _scrape_auditor_url_with_fallback is called. The fake fallback
    # returns the matching block.
    assert results, "external-county fallback should have produced a record"
    assert results[0]["owner"] == "AARON TOM"


# --- get_property_by_address / get_property_details convenience -------


@pytest.mark.asyncio
async def test_get_property_details_picks_matching_parcel():
    records = [
        {"parcel_id": "111", "owner": "A", "address": "1 Main"},
        {"parcel_id": "222", "owner": "B", "address": "2 Main"},
    ]
    with patch.object(auditor, "search_property_records", AsyncMock(return_value=records)):
        result = await get_property_details("franklin", "222", "Ohio")
    assert result["parcel_id"] == "222"
    assert result["owner"] == "B"


@pytest.mark.asyncio
async def test_get_property_details_returns_not_found_when_no_match():
    records = [{"parcel_id": "111", "owner": "A", "address": "1 Main"}]
    with patch.object(auditor, "search_property_records", AsyncMock(return_value=records)):
        result = await get_property_details("franklin", "999", "Ohio")
    assert result["status"] == "not found"


@pytest.mark.asyncio
async def test_get_property_details_returns_not_found_on_empty():
    with patch.object(auditor, "search_property_records", AsyncMock(return_value=[])):
        result = await get_property_details("franklin", "111", "Ohio")
    assert result["status"] == "not found"


@pytest.mark.asyncio
async def test_get_property_by_address_picks_token_match():
    records = [
        {"address": "500 Other St", "owner": "X"},
        {"address": "123 Main St", "owner": "Aaron Tom"},
    ]
    with patch.object(auditor, "search_property_records", AsyncMock(return_value=records)):
        result = await get_property_by_address("franklin", "123 Main St", "Ohio")
    assert result["owner"] == "Aaron Tom"


@pytest.mark.asyncio
async def test_get_property_by_address_falls_back_to_top_result():
    records = [
        {"address": "500 Other St", "owner": "X"},
        {"address": "999 Different Ave", "owner": "Y"},
    ]
    with patch.object(auditor, "search_property_records", AsyncMock(return_value=records)):
        result = await get_property_by_address("franklin", "777 Missing Rd", "Ohio")
    # No token match — return top result
    assert result == records[0]


@pytest.mark.asyncio
async def test_get_property_by_address_returns_not_found_on_empty():
    with patch.object(auditor, "search_property_records", AsyncMock(return_value=[])):
        result = await get_property_by_address("franklin", "x", "Ohio")
    assert result["status"] == "not found"


# --- TPAD retry on transient error -------------------------------------


@pytest.mark.asyncio
async def test_tpad_retries_on_httpx_error_then_succeeds():
    """TPAD path must retry once on httpx errors before giving up."""
    import httpx

    call_count = {"n": 0}

    def _make_response():
        # First call raises, second call returns 200 with empty body
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("connection reset")
        # httpx.Response 200 with empty JSON array
        from httpx import Response, Request
        return Response(200, json=[], request=Request("POST", "https://x"))

    # The TPAD scraper creates an httpx.AsyncClient internally; patch
    # the post method so the first call raises and the second succeeds.
    real_post = httpx.AsyncClient.post

    async def _patched_post(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("connection reset")
        from httpx import Response, Request
        # Second call: empty list → no records (success path, not failure)
        return Response(200, json=[], request=Request("POST", args[0] if args else "https://x"))

    with patch("httpx.AsyncClient.post", _patched_post):
        records = await auditor._search_tpad_tn(
            "anderson", "Aaron Tom", STATE_PORTALS["tennessee"]
        )
    # Empty array → 0 records, but importantly: no exception escaped
    assert records == []
    assert call_count["n"] == 2, f"expected 2 attempts (1 fail + 1 success), got {call_count['n']}"


@pytest.mark.asyncio
async def test_tpad_gives_up_after_two_failures():
    """TPAD must NOT retry forever — exactly 2 attempts then return []."""
    import httpx

    call_count = {"n": 0}

    async def _always_fail(self, *args, **kwargs):
        call_count["n"] += 1
        raise httpx.ConnectError("permanent failure")

    with patch("httpx.AsyncClient.post", _always_fail):
        records = await auditor._search_tpad_tn(
            "anderson", "Aaron Tom", STATE_PORTALS["tennessee"]
        )
    assert records == []
    assert call_count["n"] == 2, f"expected exactly 2 attempts, got {call_count['n']}"


@pytest.mark.asyncio
async def test_tpad_returns_empty_for_unknown_county():
    """An unknown TN county (not in the map, not external) returns []. The
    web-search fallback is the caller's job — not the scraper's."""
    records = await auditor._search_tpad_tn(
        "atlantis_county", "x", STATE_PORTALS["tennessee"]
    )
    assert records == []


# --- Live network tests (opt-in) ---------------------------------------


_LIVE = os.environ.get("AUDITOR_LIVE_TESTS") == "1"


@pytest.mark.asyncio
@pytest.mark.skipif(not _LIVE, reason="AUDITOR_LIVE_TESTS!=1 (requires crawl4ai + FlareSolverr + internet)")
async def test_live_tpad_search_returns_real_record():
    """End-to-end smoke against the real TN TPAD portal."""
    rows = await search_property_records("anderson", "Tom", "owner", "Tennessee")
    assert rows, "expected at least one TPAD match for 'Tom' in Anderson County"
    sample = rows[0]
    assert sample["source"] == "tpad"
    assert sample["state"] == "Tennessee"
    assert sample["owner"] or sample["address"], f"missing identifying fields: {sample}"
