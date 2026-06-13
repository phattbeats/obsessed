"""Tests for the Legacy.com / Columbus Dispatch obituary scrapers (PHA-820).

Fixtures are real JSON-LD captured live from Legacy.com on 2026-06-13 and inlined
here so the suite is self-contained (the repo's `data/` dir is gitignored). The
DataDome solve path is exercised with mocks: a challenge body on the first fetch,
then a solved cookie, then the real HTML on retry — plus the inert path when
DATADOME_SOLVE_PROXY is unset.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.scraper import captcha_solver, datadome, dispatch, legacy_com


# Real SearchResultsPage JSON-LD (ItemList of obituary links), trimmed to 3 rows.
_SEARCH_LD = '{"@context":"https://schema.org","@type":"SearchResultsPage","name":"Find an Obituary - Legacy.com","mainEntity":{"@type":"ItemList","itemListElement":[{"position":1,"@type":"ListItem","url":"https://www.legacy.com/person/Jane-Downs-Ovitz-61477294","name":"Jane Downs Ovitz Obituary"},{"position":2,"@type":"ListItem","url":"https://www.legacy.com/person/Carolyn-Escalante-61554060","name":"Carolyn Escalante Obituary"},{"position":3,"@type":"ListItem","url":"https://www.legacy.com/person/Shirley-Hedding-61555320","name":"Shirley Hedding Obituary"}]}}'
SEARCH_HTML = f'<!doctype html><html><head><script type="application/ld+json">{_SEARCH_LD}</script></head><body>results</body></html>'

# Real CreativeWork+Person JSON-LD from a /person detail page.
_DETAIL_LD = '{"@context":"https://schema.org","@type":"CreativeWork","name":"Online Memorial for John Smith","mainEntityOfPage":"https://www.legacy.com/person/John-Smith-60959660","text":"This online memorial is dedicated to the memory of John Smith.","url":"https://www.legacy.com/person/John-Smith-60959660","about":{"@type":"Person","name":"John Smith","givenName":"John","familyName":"Smith","birthDate":"1939-05-17","deathDate":"2026-03-17","image":"https://www.legacy.com/media.legacy.net/obituary/image/392e3ff0.jpg","description":"John Smith Obituary and Online Memorial (2026).","sameAs":["https://www.legacy.com/obituaries/name/Smith/John"]}}'
DETAIL_HTML = f'<!doctype html><html><head><script type="application/ld+json">{_DETAIL_LD}</script></head><body>obit</body></html>'

# Minimal real-shape DataDome 403 challenge stub.
_DD_CHALLENGE_HTML = (
    "<html><head><script>var dd={'rt':'i','cid':'AHrlqAAAAAMA_RCYRm83ZHU"
    "AF_Vt_A==','hsh':'BA0C85CB01834060078D21FA9FBE55','t':'fe','s':50779,"
    "'host':'geo.captcha-delivery.com'}</script></head><body>blocked</body></html>"
)


@pytest.fixture(autouse=True)
def _reset_datadome_state():
    datadome.reset_run_state()
    yield
    datadome.reset_run_state()


# --- JSON-LD parsers ------------------------------------------------------


def test_parse_search_results_extracts_obituary_rows():
    html = SEARCH_HTML
    rows = legacy_com.parse_search_results(html)
    assert rows, "expected obituary rows from the SearchResultsPage ItemList"
    first = rows[0]
    assert first["name"] == "Jane Downs Ovitz"  # " Obituary" suffix stripped
    assert first["url"].startswith("https://www.legacy.com/person/")
    assert first["position"] == 1


def test_parse_search_results_dedupes_by_url():
    html = SEARCH_HTML
    rows = legacy_com.parse_search_results(html)
    urls = [r["url"] for r in rows]
    assert len(urls) == len(set(urls))


def test_parse_search_results_empty_without_json_ld():
    assert legacy_com.parse_search_results("<html><body>nope</body></html>") == []


def test_parse_obituary_detail_extracts_person_fields():
    html = DETAIL_HTML
    obit = legacy_com.parse_obituary_detail(html)
    assert obit is not None
    assert obit["name"] == "John Smith"
    assert obit["given_name"] == "John"
    assert obit["family_name"] == "Smith"
    assert obit["birth_date"] == "1939-05-17"
    assert obit["death_date"] == "2026-03-17"
    assert obit["image"].startswith("https://")
    assert obit["text"]  # memorial / obituary text present


def test_parse_obituary_detail_none_without_person():
    assert legacy_com.parse_obituary_detail("<html><body>x</body></html>") is None


# Real affiliate /name/<slug> detail shape: NewsArticle.articleBody + top-level Person.
_AFFILIATE_LD_ARTICLE = '{"@context":"http://schema.org","@type":"NewsArticle","articleBody":"Worthington - Neil Gant, 75, of Worthington, OH died on May 5, 2026.","author":{"@type":"Person","name":"The Columbus Dispatch"}}'
_AFFILIATE_LD_PERSON = '{"@context":"http://schema.org","@type":"Person","birthDate":"1951-3-1","deathDate":"2026-5-5","deathPlace":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Worthington","addressRegion":"OH"}},"familyName":"Gant","givenName":"Neil","name":"Neil Gant"}'
AFFILIATE_DETAIL_HTML = (
    '<!doctype html><html><head>'
    f'<script type="application/ld+json">{_AFFILIATE_LD_ARTICLE}</script>'
    f'<script type="application/ld+json">{_AFFILIATE_LD_PERSON}</script>'
    '</head><body>obit</body></html>'
)


def test_parse_obituary_detail_handles_affiliate_newsarticle_shape():
    obit = legacy_com.parse_obituary_detail(AFFILIATE_DETAIL_HTML)
    assert obit is not None
    assert obit["name"] == "Neil Gant"
    assert obit["given_name"] == "Neil"
    assert obit["death_date"] == "2026-5-5"
    assert obit["death_place"] == {"locality": "Worthington", "region": "OH"}
    assert obit["text"].startswith("Worthington - Neil Gant")  # full articleBody


def test_build_search_url_encodes_params():
    url = legacy_com.build_search_url("John", "Smith", "Ohio")
    assert url == (
        "https://www.legacy.com/obituaries/search"
        "?firstName=John&lastName=Smith&state=Ohio"
    )


def test_build_search_url_name_only():
    url = legacy_com.build_search_url(None, "Smith")
    assert url == "https://www.legacy.com/obituaries/search?lastName=Smith"


# --- DataDome solve path --------------------------------------------------


def test_search_obituaries_parses_when_no_challenge():
    html = SEARCH_HTML
    fetch_mock = AsyncMock(return_value=(html, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(legacy_com.search_obituaries("John", "Smith", "Ohio"))
    assert result["wall"] is None
    assert result["results"][0]["name"] == "Jane Downs Ovitz"
    assert result["results"][0]["source"] == "legacy.com"


def test_search_obituaries_inert_when_proxy_missing():
    """Challenge hit + no proxy -> typed wall, no paid solve, empty results."""
    fetch_mock = AsyncMock(return_value=(_DD_CHALLENGE_HTML, 403))
    solve_mock = AsyncMock()
    render_mock = AsyncMock(return_value=("", 0))  # render also unavailable
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(datadome.settings, "datadome_solve_proxy", ""), \
         patch.object(legacy_com, "crawl4ai_fetch_html", render_mock), \
         patch.object(captcha_solver, "solve_datadome", solve_mock):
        result = asyncio.run(legacy_com.search_obituaries("John", "Smith"))
    assert solve_mock.await_count == 0
    assert result["results"] == []
    assert result["wall"]["kind"] == "datadome_proxy_missing"


def test_search_obituaries_solves_then_parses_when_configured():
    html = SEARCH_HTML
    # First call -> challenge; second (post-solve) -> real HTML.
    fetch_mock = AsyncMock(side_effect=[(_DD_CHALLENGE_HTML, 403), (html, 200)])
    solve_mock = AsyncMock(return_value="SOLVED_DD_COOKIE")
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(datadome.settings, "datadome_solve_proxy", "10.0.0.100:8118"), \
         patch.object(captcha_solver.settings, "twocaptcha_api_key", "key-xyz"), \
         patch.object(captcha_solver, "solve_datadome", solve_mock):
        result = asyncio.run(legacy_com.search_obituaries("John", "Smith"))

    assert solve_mock.await_count == 1
    # Retry fetch carried the solved cookie.
    assert fetch_mock.await_args.kwargs.get("datadome_cookie") == "SOLVED_DD_COOKIE"
    assert result["wall"] is None
    assert result["results"][0]["name"] == "Jane Downs Ovitz"


def test_get_obituary_returns_detail():
    html = DETAIL_HTML
    fetch_mock = AsyncMock(return_value=(html, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(
            legacy_com.get_obituary("https://www.legacy.com/person/John-Smith-60959660")
        )
    assert result["wall"] is None
    assert result["obituary"]["name"] == "John Smith"
    assert result["obituary"]["death_date"] == "2026-03-17"


def test_solve_limit_caps_paid_solves():
    """Per-run cap stops the second solve from being attempted."""
    fetch_mock = AsyncMock(return_value=(_DD_CHALLENGE_HTML, 403))
    solve_mock = AsyncMock(return_value="COOKIE")
    render_mock = AsyncMock(return_value=("", 0))
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(datadome.settings, "datadome_solve_proxy", "10.0.0.100:8118"), \
         patch.object(datadome.settings, "datadome_max_solves_per_run", 1), \
         patch.object(legacy_com, "crawl4ai_fetch_html", render_mock), \
         patch.object(captcha_solver.settings, "twocaptcha_api_key", "key-xyz"), \
         patch.object(captcha_solver, "solve_datadome", solve_mock):
        # First search: solves (1), but retry still challenged -> datadome_challenge.
        r1 = asyncio.run(legacy_com.search_obituaries("A", "One"))
        # Second search: cap already reached -> datadome_solve_limit, no new solve.
        r2 = asyncio.run(legacy_com.search_obituaries("B", "Two"))
    assert solve_mock.await_count == 1
    assert r1["wall"]["kind"] == "datadome_challenge"
    assert r2["wall"]["kind"] == "datadome_solve_limit"


# --- Dispatch facade ------------------------------------------------------


def test_dispatch_browse_recent_tags_source():
    html = SEARCH_HTML
    fetch_mock = AsyncMock(return_value=(html, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(dispatch.browse_recent())
    assert result["source"] == "dispatch.com"
    assert result["url"] == dispatch.DISPATCH_AFFILIATE_URL
    assert result["results"][0]["source"] == "dispatch.com"


def test_dispatch_search_scopes_to_ohio():
    html = SEARCH_HTML
    fetch_mock = AsyncMock(return_value=(html, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(dispatch.search("John", "Smith"))
    assert result["source"] == "dispatch.com"
    assert "state=Ohio" in result["url"]
    assert result["results"][0]["source"] == "dispatch.com"


def test_dispatch_browse_recent_inert_when_proxy_missing():
    fetch_mock = AsyncMock(return_value=(_DD_CHALLENGE_HTML, 403))
    render_mock = AsyncMock(return_value=("", 0))
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(datadome.settings, "datadome_solve_proxy", ""), \
         patch.object(legacy_com, "crawl4ai_fetch_html", render_mock):
        result = asyncio.run(dispatch.browse_recent())
    assert result["results"] == []
    assert result["wall"]["kind"] == "datadome_proxy_missing"


# --- crawl4ai render escalation (the standalone, no-paid-proxy bypass) -----


_CF_BLOCK_HTML = "<html><head><title>Just a moment...</title></head><body></body></html>"


def test_render_escalation_recovers_dispatch_feed_when_direct_is_cloudflare_walled():
    """Direct fetch hits Cloudflare (403); crawl4ai render returns the real feed."""
    fetch_mock = AsyncMock(return_value=(_CF_BLOCK_HTML, 403))
    render_mock = AsyncMock(return_value=(SEARCH_HTML, 200))
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(legacy_com, "crawl4ai_fetch_html", render_mock):
        result = asyncio.run(dispatch.browse_recent())
    assert render_mock.await_count == 1
    assert result["wall"] is None
    assert result["results"][0]["source"] == "dispatch.com"
    assert result["results"][0]["name"] == "Jane Downs Ovitz"


def test_render_escalation_surfaces_wall_when_render_also_blocked():
    """Direct Cloudflare-walled and render also returns a challenge -> wall, no fake data."""
    fetch_mock = AsyncMock(return_value=(_CF_BLOCK_HTML, 403))
    render_mock = AsyncMock(return_value=(_CF_BLOCK_HTML, 403))
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(legacy_com, "crawl4ai_fetch_html", render_mock):
        result = asyncio.run(legacy_com.search_obituaries("John", "Smith"))
    assert result["results"] == []
    assert result["wall"]["kind"] == "upstream_blocked"
