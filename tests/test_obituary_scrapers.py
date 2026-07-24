"""Tests for the obituary scrapers (PHA-1349): Legacy.com, Columbus Dispatch,
and Find a Grave.

Legacy/Dispatch fixtures are real JSON-LD captured live from Legacy.com on
2026-06-13 (ported from the PHA-820 branch); Find a Grave fixtures are real
markup captured live via browserless on 2026-07-11 and trimmed to the
relevant blocks. All are inlined so the suite is self-contained (the repo's
`data/` dir is gitignored). The DataDome solve path is exercised with mocks:
a challenge body on the first fetch, then a solved cookie, then the real HTML
on retry — plus the inert path when DATADOME_SOLVE_PROXY is unset.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.scraper import captcha_solver, datadome, obituary


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

_CF_BLOCK_HTML = "<html><head><title>Just a moment...</title></head><body></body></html>"


@pytest.fixture(autouse=True)
def _reset_datadome_state():
    datadome.reset_run_state()
    yield
    datadome.reset_run_state()


# ===========================================================================
# Legacy.com — JSON-LD parsers
# ===========================================================================


def test_legacy_parse_search_results_extracts_obituary_rows():
    rows = obituary.legacy_parse_search_results(SEARCH_HTML)
    assert rows, "expected obituary rows from the SearchResultsPage ItemList"
    first = rows[0]
    assert first["name"] == "Jane Downs Ovitz"  # " Obituary" suffix stripped
    assert first["url"].startswith("https://www.legacy.com/person/")
    assert first["position"] == 1


def test_legacy_parse_search_results_dedupes_by_url():
    rows = obituary.legacy_parse_search_results(SEARCH_HTML)
    urls = [r["url"] for r in rows]
    assert len(urls) == len(set(urls))


def test_legacy_parse_search_results_empty_without_json_ld():
    assert obituary.legacy_parse_search_results("<html><body>nope</body></html>") == []


def test_legacy_parse_obituary_detail_extracts_person_fields():
    obit = obituary.legacy_parse_obituary_detail(DETAIL_HTML)
    assert obit is not None
    assert obit["name"] == "John Smith"
    assert obit["given_name"] == "John"
    assert obit["family_name"] == "Smith"
    assert obit["birth_date"] == "1939-05-17"
    assert obit["death_date"] == "2026-03-17"
    assert obit["image"].startswith("https://")
    assert obit["text"]  # memorial / obituary text present


def test_legacy_parse_obituary_detail_none_without_person():
    assert obituary.legacy_parse_obituary_detail("<html><body>x</body></html>") is None


# Real affiliate /name/<slug> detail shape: NewsArticle.articleBody + top-level Person.
_AFFILIATE_LD_ARTICLE = '{"@context":"http://schema.org","@type":"NewsArticle","articleBody":"Worthington - Neil Gant, 75, of Worthington, OH died on May 5, 2026.","author":{"@type":"Person","name":"The Columbus Dispatch"}}'
_AFFILIATE_LD_PERSON = '{"@context":"http://schema.org","@type":"Person","birthDate":"1951-3-1","deathDate":"2026-5-5","deathPlace":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Worthington","addressRegion":"OH"}},"familyName":"Gant","givenName":"Neil","name":"Neil Gant"}'
AFFILIATE_DETAIL_HTML = (
    '<!doctype html><html><head>'
    f'<script type="application/ld+json">{_AFFILIATE_LD_ARTICLE}</script>'
    f'<script type="application/ld+json">{_AFFILIATE_LD_PERSON}</script>'
    '</head><body>obit</body></html>'
)


def test_legacy_parse_obituary_detail_handles_affiliate_newsarticle_shape():
    obit = obituary.legacy_parse_obituary_detail(AFFILIATE_DETAIL_HTML)
    assert obit is not None
    assert obit["name"] == "Neil Gant"
    assert obit["given_name"] == "Neil"
    assert obit["death_date"] == "2026-5-5"
    assert obit["death_place"] == {"locality": "Worthington", "region": "OH"}
    assert obit["text"].startswith("Worthington - Neil Gant")  # full articleBody


def test_legacy_build_search_url_encodes_params():
    url = obituary.legacy_build_search_url("John", "Smith", "Ohio")
    assert url == (
        "https://www.legacy.com/obituaries/search"
        "?firstName=John&lastName=Smith&state=Ohio"
    )


def test_legacy_build_search_url_name_only():
    url = obituary.legacy_build_search_url(None, "Smith")
    assert url == "https://www.legacy.com/obituaries/search?lastName=Smith"


# ===========================================================================
# Legacy.com — DataDome solve path
# ===========================================================================


def test_legacy_search_parses_when_no_challenge():
    fetch_mock = AsyncMock(return_value=(SEARCH_HTML, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(obituary.legacy_search("John", "Smith", "Ohio"))
    assert result["wall"] is None
    assert result["results"][0]["name"] == "Jane Downs Ovitz"
    assert result["results"][0]["source"] == "legacy.com"


def test_legacy_search_inert_when_proxy_missing():
    """Challenge hit + no proxy -> typed wall, no paid solve, empty results."""
    fetch_mock = AsyncMock(return_value=(_DD_CHALLENGE_HTML, 403))
    solve_mock = AsyncMock()
    render_mock = AsyncMock(return_value=("", 0))  # render also unavailable
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(datadome.settings, "datadome_solve_proxy", ""), \
         patch.object(obituary, "crawl4ai_fetch_html", render_mock), \
         patch.object(captcha_solver, "solve_datadome", solve_mock):
        result = asyncio.run(obituary.legacy_search("John", "Smith"))
    assert solve_mock.await_count == 0
    assert result["results"] == []
    assert result["wall"]["kind"] == "datadome_proxy_missing"


def test_legacy_search_solves_then_parses_when_configured():
    # First call -> challenge; second (post-solve) -> real HTML.
    fetch_mock = AsyncMock(side_effect=[(_DD_CHALLENGE_HTML, 403), (SEARCH_HTML, 200)])
    solve_mock = AsyncMock(return_value="SOLVED_DD_COOKIE")
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(datadome.settings, "datadome_solve_proxy", "10.0.0.100:8118"), \
         patch.object(captcha_solver.settings, "twocaptcha_api_key", "key-xyz"), \
         patch.object(captcha_solver, "solve_datadome", solve_mock):
        result = asyncio.run(obituary.legacy_search("John", "Smith"))

    assert solve_mock.await_count == 1
    # Retry fetch carried the solved cookie.
    assert fetch_mock.await_args.kwargs.get("datadome_cookie") == "SOLVED_DD_COOKIE"
    assert result["wall"] is None
    assert result["results"][0]["name"] == "Jane Downs Ovitz"


def test_legacy_get_obituary_returns_detail():
    fetch_mock = AsyncMock(return_value=(DETAIL_HTML, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(
            obituary.legacy_get_obituary("https://www.legacy.com/person/John-Smith-60959660")
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
         patch.object(obituary, "crawl4ai_fetch_html", render_mock), \
         patch.object(captcha_solver.settings, "twocaptcha_api_key", "key-xyz"), \
         patch.object(captcha_solver, "solve_datadome", solve_mock):
        # First search: solves (1), but retry still challenged -> datadome_challenge.
        r1 = asyncio.run(obituary.legacy_search("A", "One"))
        # Second search: cap already reached -> datadome_solve_limit, no new solve.
        r2 = asyncio.run(obituary.legacy_search("B", "Two"))
    assert solve_mock.await_count == 1
    assert r1["wall"]["kind"] == "datadome_challenge"
    assert r2["wall"]["kind"] == "datadome_solve_limit"


# ===========================================================================
# Dispatch facade
# ===========================================================================


def test_dispatch_browse_recent_tags_source():
    fetch_mock = AsyncMock(return_value=(SEARCH_HTML, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(obituary.dispatch_browse_recent())
    assert result["source"] == "dispatch.com"
    assert result["url"] == obituary.DISPATCH_AFFILIATE_URL
    assert result["results"][0]["source"] == "dispatch.com"


def test_dispatch_search_scopes_to_ohio():
    fetch_mock = AsyncMock(return_value=(SEARCH_HTML, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(obituary.dispatch_search("John", "Smith"))
    assert result["source"] == "dispatch.com"
    assert "state=Ohio" in result["url"]
    assert result["results"][0]["source"] == "dispatch.com"


def test_dispatch_browse_recent_inert_when_proxy_missing():
    fetch_mock = AsyncMock(return_value=(_DD_CHALLENGE_HTML, 403))
    render_mock = AsyncMock(return_value=("", 0))
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(datadome.settings, "datadome_solve_proxy", ""), \
         patch.object(obituary, "crawl4ai_fetch_html", render_mock):
        result = asyncio.run(obituary.dispatch_browse_recent())
    assert result["results"] == []
    assert result["wall"]["kind"] == "datadome_proxy_missing"


# ===========================================================================
# crawl4ai render escalation (the standalone, no-paid-proxy bypass)
# ===========================================================================


def test_render_escalation_recovers_dispatch_feed_when_direct_is_cloudflare_walled():
    """Direct fetch hits Cloudflare (403); crawl4ai render returns the real feed."""
    fetch_mock = AsyncMock(return_value=(_CF_BLOCK_HTML, 403))
    render_mock = AsyncMock(return_value=(SEARCH_HTML, 200))
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(obituary, "crawl4ai_fetch_html", render_mock):
        result = asyncio.run(obituary.dispatch_browse_recent())
    assert render_mock.await_count == 1
    assert result["wall"] is None
    assert result["results"][0]["source"] == "dispatch.com"
    assert result["results"][0]["name"] == "Jane Downs Ovitz"


def test_render_escalation_surfaces_wall_when_render_also_blocked():
    """Direct Cloudflare-walled and render also returns a challenge -> wall, no fake data."""
    fetch_mock = AsyncMock(return_value=(_CF_BLOCK_HTML, 403))
    render_mock = AsyncMock(return_value=(_CF_BLOCK_HTML, 403))
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(obituary, "crawl4ai_fetch_html", render_mock):
        result = asyncio.run(obituary.legacy_search("John", "Smith"))
    assert result["results"] == []
    assert result["wall"]["kind"] == "upstream_blocked"


# ===========================================================================
# Find a Grave
# ===========================================================================
#
# Real markup captured live via browserless 2026-07-11 (crawl4ai was
# unreachable that session; browserless's residential egress rendered these
# pages clean). Trimmed to the relevant blocks. A bare container GET hits a
# generic Cloudflare "Just a moment..." interstitial here, not a DataDome
# stub, so these ride `_resolve_html`'s generic-block branch straight to the
# crawl4ai render escalation.

FINDAGRAVE_SEARCH_HTML = """
<div class="memorial-item px-2 py-2 gx-4 gy-0 position-relative row border-bottom align-items-md-center" id="sr-107597382">
  <div class="col-12 col-md col-print-3">
    <a class="d-flex align-items-center text-decoration-none" href="/memorial/107597382/john-smith">
      <div class="memorial-item--info">
        <div class="memorial-item---grave">
          <h2 class="name-grave d-flex"><i class="pe-2 text-break">John Smith</i></h2>
          <b class="birthDeathDates fw-light fs-5 text-body">12 Dec 1943 &#8211; 9 Jun 1994</b>
        </div>
      </div>
    </a>
  </div>
  <div class="memorial-item---cemet col-12 col-md-auto">
    <form action="/cemetery/66059/saint-marys-cemetery"><button type="submit" class="btn btn-link" title="Saint Marys Cemetery">Saint Marys Cemetery</button></form>
    <p class="addr-cemet mb-1">
      Yonkers,


      Westchester County,


      New York
    </p>
  </div>
</div>
"""

FINDAGRAVE_MEMORIAL_HTML = """
<html><head>
<link rel="canonical" href="https://www.findagrave.com/memorial/3090/james_h-kingsley">
</head><body>
<h1 id="bio-name" class="bio-name" itemprop="name">James H. Kingsley <span class="visually-hidden">Famous memorial</span></h1>
<dl class="mem-events">
  <dt><span id="birthLabel">Birth</span></dt>
  <dd><time id="birthDateLabel" itemprop="birthDate">4 Nov 1937</time>
    <div id="birthLocationLabel" itemprop="birthPlace">
      Memphis, Shelby County, Tennessee, USA
    </div>
  </dd>
  <dt><span id="deathLabel">Death</span></dt>
  <dd><span id="deathDateLabel" itemprop="deathDate">22 Feb 1989 (aged 51)</span>
    <div id="deathLocationLabel" itemprop="deathPlace">
      Walls, DeSoto County, Mississippi, USA
    </div>
  </dd>
  <dt><span id="cemeteryLabel">Burial</span></dt>
  <dd>
    <div itemscope itemtype="https://schema.org/Cemetery">
      <a href="/cemetery/15594/memphis-funeral-home-and-memorial-gardens" itemprop="url">
        <span id="cemeteryNameLabel" itemprop="name">Memphis Funeral Home and Memorial Gardens</span>
      </a>
    </div>
    <span itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
      <span id="cemeteryCityName" itemprop="addressLocality">Bartlett</span>,
      <span id="cemeteryCountyName">Shelby County</span>,
      <span id="cemeteryStateName" itemprop="addressRegion">Tennessee</span>,
      <span id="cemeteryCountryName" itemprop="addressLocality">USA</span>
    </span>
  </dd>
</dl>
<div class="overview-panel data-bio">
  <div id="fullBio">Actor. He began his career as a movie stunt man in the 1950s.</div>
</div>
<div class="overview-panel data-family">
  <div class="overview-panel--body" id="family-grid">
    <div class="col-12 col-sm-6 col-print-auto">
      <b id="parentsLabel" class="label-relation">Parents</b>
      <ul class="member-family" aria-labelledby="parentsLabel">
        <li itemscope itemtype="https://schema.org/Person">
          <a href="/memorial/159405767/luke_augustus-kingsley" itemprop="url">
            <h3 itemprop="name">Luke Augustus  Kingsley Sr</h3>
            <p><span itemprop="birthDate">1889</span>&#8211;<span itemprop="deathDate">1984</span></p>
          </a>
        </li>
      </ul>
    </div>
    <div class="col-12 col-sm-6 col-print-auto">
      <b id="siblingsLabel" class="label-relation">Siblings</b>
      <ul class="member-family" aria-labelledby="siblingsLabel">
        <li itemscope itemtype="https://schema.org/Person">
          <a href="/memorial/953828/margaret-martin" itemprop="url">
            <h3 itemprop="name">Margaret  Kingsley Martin</h3>
            <p><span itemprop="birthDate">1935</span>&#8211;<span itemprop="deathDate">1989</span></p>
          </a>
        </li>
      </ul>
    </div>
  </div>
</div>
</body></html>
"""

FINDAGRAVE_EMPTY_FAMILY_HTML = """
<html><body>
<h1 id="bio-name" itemprop="name">John Smith</h1>
<div class="data-family empty"></div>
</body></html>
"""


def test_findagrave_parse_search_results_extracts_rows():
    rows = obituary.findagrave_parse_search_results(FINDAGRAVE_SEARCH_HTML)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "John Smith"
    assert row["memorial_id"] == "107597382"
    assert row["url"] == "https://www.findagrave.com/memorial/107597382/john-smith"
    assert row["birth_date"] == "12 Dec 1943"
    assert row["death_date"] == "9 Jun 1994"
    assert row["cemetery"] == "Saint Marys Cemetery"
    assert row["cemetery_url"] == "https://www.findagrave.com/cemetery/66059/saint-marys-cemetery"
    assert row["location"] == "Yonkers, Westchester County, New York"
    assert row["source"] == "findagrave.com"


def test_findagrave_parse_search_results_empty_without_rows():
    assert obituary.findagrave_parse_search_results("<html><body>nope</body></html>") == []


def test_findagrave_parse_memorial_extracts_record_and_bio():
    mem = obituary.findagrave_parse_memorial(FINDAGRAVE_MEMORIAL_HTML)
    assert mem is not None
    assert mem["memorial_id"] == "3090"
    assert mem["name"] == "James H. Kingsley"
    assert mem["birth_date"] == "4 Nov 1937"
    assert mem["birth_place"] == "Memphis, Shelby County, Tennessee, USA"
    assert mem["death_date"] == "22 Feb 1989 (aged 51)"
    assert mem["death_place"] == "Walls, DeSoto County, Mississippi, USA"
    assert mem["cemetery"]["name"] == "Memphis Funeral Home and Memorial Gardens"
    assert mem["cemetery"]["city"] == "Bartlett"
    assert mem["cemetery"]["state"] == "Tennessee"
    assert "stunt man" in mem["bio"]
    assert mem["source"] == "findagrave.com"


def test_findagrave_parse_memorial_extracts_family_relationships():
    mem = obituary.findagrave_parse_memorial(FINDAGRAVE_MEMORIAL_HTML)
    assert mem["family"]["parents"] == [
        {
            "name": "Luke Augustus Kingsley Sr",
            "memorial_id": "159405767",
            "url": "https://www.findagrave.com/memorial/159405767/luke_augustus-kingsley",
            "birth_year": "1889",
            "death_year": "1984",
        }
    ]
    assert mem["family"]["siblings"][0]["name"] == "Margaret Kingsley Martin"
    assert mem["family"]["siblings"][0]["memorial_id"] == "953828"


def test_findagrave_parse_memorial_no_family_section():
    mem = obituary.findagrave_parse_memorial(FINDAGRAVE_EMPTY_FAMILY_HTML)
    assert mem is not None
    assert mem["family"] == {}


def test_findagrave_parse_memorial_none_without_bio_name():
    assert obituary.findagrave_parse_memorial("<html><body>nope</body></html>") is None


def test_findagrave_build_search_url_encodes_params():
    url = obituary.findagrave_build_search_url("John", "Smith", "Ohio")
    assert url == (
        "https://www.findagrave.com/memorial/search"
        "?firstname=John&lastname=Smith&location=Ohio"
    )


def test_findagrave_search_parses_when_no_challenge():
    fetch_mock = AsyncMock(return_value=(FINDAGRAVE_SEARCH_HTML, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(obituary.findagrave_search("John", "Smith"))
    assert result["wall"] is None
    assert result["source"] == "findagrave.com"
    assert result["results"][0]["memorial_id"] == "107597382"


def test_findagrave_get_memorial_returns_detail():
    fetch_mock = AsyncMock(return_value=(FINDAGRAVE_MEMORIAL_HTML, 200))
    with patch.object(datadome, "_fetch", fetch_mock):
        result = asyncio.run(
            obituary.findagrave_get_memorial("https://www.findagrave.com/memorial/3090/james_h-kingsley")
        )
    assert result["wall"] is None
    assert result["memorial"]["name"] == "James H. Kingsley"
    assert result["memorial"]["family"]["parents"]


def test_findagrave_escalates_to_render_on_generic_cloudflare_block():
    """The observed live behavior: no DataDome stub, a plain CF interstitial."""
    fetch_mock = AsyncMock(return_value=(_CF_BLOCK_HTML, 403))
    render_mock = AsyncMock(return_value=(FINDAGRAVE_MEMORIAL_HTML, 200))
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(obituary, "crawl4ai_fetch_html", render_mock):
        result = asyncio.run(
            obituary.findagrave_get_memorial("https://www.findagrave.com/memorial/3090/james_h-kingsley")
        )
    assert render_mock.await_count == 1
    assert result["wall"] is None
    assert result["memorial"]["name"] == "James H. Kingsley"


def test_findagrave_surfaces_wall_when_render_also_blocked():
    fetch_mock = AsyncMock(return_value=(_CF_BLOCK_HTML, 403))
    render_mock = AsyncMock(return_value=(_CF_BLOCK_HTML, 403))
    with patch.object(datadome, "_fetch", fetch_mock), \
         patch.object(obituary, "crawl4ai_fetch_html", render_mock):
        result = asyncio.run(obituary.findagrave_search("John", "Smith"))
    assert result["results"] == []
    assert result["wall"]["kind"] == "upstream_blocked"
