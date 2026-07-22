"""Tests for the FastPeopleSearch scraper (PHA-795).

The listing parser is exercised against the JSON-LD captured from a real FPS
listing page (PHA-787, `data/pha787_round3/`). Only the `application/ld+json`
blocks drive `parse_listing_people`, so the committed fixture under
`tests/fixtures/people_search/` carries just those blocks (the full 415KB
capture lives in the gitignored `data/` dir and never reached CI — PHA-1042).
The detail-page captcha path is exercised with mocks: the first `fs_get`
returns a Turnstile challenge page, then `solve_turnstile` returns a token,
then a follow-up `fs_post` returns the real detail HTML.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.scraper import captcha_solver, people_search


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "people_search"
LISTING_FIXTURE = FIXTURE_DIR / "fastpeoplesearch_aaron_tom_oh.html"


# --- Listing parser -------------------------------------------------------


def test_parse_listing_people_extracts_person_records():
    html = LISTING_FIXTURE.read_text(encoding="utf-8", errors="replace")
    people = people_search.parse_listing_people(html)
    assert people, "expected at least one Person record from listing fixture"
    first = people[0]
    assert first["name"].lower().startswith("aaron")
    assert first["url"].startswith("https://www.fastpeoplesearch.com/")
    assert first["source"] == "fastpeoplesearch"
    # Each person should carry at least one address dict (locality+region keys present)
    assert any(addr.get("region") for addr in first["addresses"])


def test_parse_listing_people_dedupes_by_url():
    html = LISTING_FIXTURE.read_text(encoding="utf-8", errors="replace")
    people = people_search.parse_listing_people(html)
    urls = [p["url"] for p in people if p["url"]]
    assert len(urls) == len(set(urls)), "Person records should be deduped by @id/url"


def test_parse_listing_people_returns_empty_on_no_json_ld():
    assert people_search.parse_listing_people("<html><body>nothing here</body></html>") == []


def test_build_search_url_includes_city_and_state():
    url = people_search._build_search_url("Aaron", "Tom", state="OH", city="Hilliard")
    assert url.endswith("/name/aaron-tom_hilliard-oh")


def test_build_search_url_handles_name_only():
    url = people_search._build_search_url("Jane", "Doe", state=None, city=None)
    assert url.endswith("/name/jane-doe")


# --- search_people -------------------------------------------------------


def test_search_people_uses_flaresolverr_then_parses_listing():
    html = LISTING_FIXTURE.read_text(encoding="utf-8", errors="replace")
    fs_get_mock = AsyncMock(return_value=(html, "ok"))
    with patch.object(people_search, "fs_get", fs_get_mock):
        people = asyncio.run(
            people_search.search_people("Aaron", "Tom", state="OH", use_flaresolverr=True)
        )
    assert fs_get_mock.await_count == 1
    called_url = fs_get_mock.await_args.args[0]
    assert called_url == "https://www.fastpeoplesearch.com/name/aaron-tom_oh"
    assert people and people[0]["name"].lower().startswith("aaron")


def test_search_people_falls_back_to_direct_on_cloudflare_wall():
    html = LISTING_FIXTURE.read_text(encoding="utf-8", errors="replace")
    fs_get_mock = AsyncMock(side_effect=people_search.CloudflareWallError("blocked", 0))
    direct_mock = AsyncMock(return_value=(html, 200))
    with patch.object(people_search, "fs_get", fs_get_mock), \
         patch.object(people_search, "_fetch_direct", direct_mock):
        people = asyncio.run(
            people_search.search_people("Aaron", "Tom", state="OH", use_flaresolverr=True)
        )
    assert fs_get_mock.await_count == 1
    assert direct_mock.await_count == 1
    assert people  # parser ran on the direct-fetch HTML


# --- get_person_detail ---------------------------------------------------


_TURNSTILE_CHALLENGE_HTML = """<!doctype html>
<html><body>
  <h1>Please verify you are human</h1>
  <div class="cf-turnstile" data-sitekey="0x4AAAAAAATESTKEY" data-callback="onTs"></div>
  <script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
</body></html>"""

_REAL_DETAIL_HTML = """<!doctype html>
<html><body>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "Person",
  "@id": "https://www.fastpeoplesearch.com/aaron-tom_id_G2456049972195159401",
  "url": "https://www.fastpeoplesearch.com/aaron-tom_id_G2456049972195159401",
  "name": "Aaron Tom",
  "HomeLocation": [{
    "@type": "Place",
    "address": {"@type": "PostalAddress", "addressLocality": "Hilliard", "addressRegion": "OH", "postalCode": "43026"}
  }]
}
</script>
</body></html>"""


def test_get_person_detail_solves_turnstile_then_reissues():
    fs_get_mock = AsyncMock(return_value=(_TURNSTILE_CHALLENGE_HTML, "ok"))
    fs_post_mock = AsyncMock(return_value=(_REAL_DETAIL_HTML, "ok"))
    solve_mock = AsyncMock(return_value="0.TURNSTILE_TOKEN")

    with patch.object(people_search, "fs_get", fs_get_mock), \
         patch.object(people_search, "fs_post", fs_post_mock), \
         patch.object(captcha_solver.settings, "twocaptcha_api_key", "key-xyz"), \
         patch.object(captcha_solver, "solve_turnstile", solve_mock):
        result = asyncio.run(
            people_search.get_person_detail(
                "https://www.fastpeoplesearch.com/aaron-tom_id_G2456049972195159401",
                use_flaresolverr=True,
                use_captcha=True,
            )
        )

    assert solve_mock.await_count == 1
    site_key_arg, page_url_arg = solve_mock.await_args.args
    assert site_key_arg == "0x4AAAAAAATESTKEY"
    assert page_url_arg.endswith("G2456049972195159401")

    assert fs_post_mock.await_count == 1
    post_kwargs = fs_post_mock.await_args.kwargs
    assert post_kwargs["post_data"] == {"cf-turnstile-response": "0.TURNSTILE_TOKEN"}

    assert result["turnstile_pending"] is False
    assert result["person"] is not None
    assert result["person"]["name"] == "Aaron Tom"
    assert result["person"]["addresses"][0]["region"] == "OH"


def test_get_person_detail_skips_captcha_gracefully_when_key_empty():
    fs_get_mock = AsyncMock(return_value=(_TURNSTILE_CHALLENGE_HTML, "ok"))
    fs_post_mock = AsyncMock()
    solve_mock = AsyncMock()

    with patch.object(people_search, "fs_get", fs_get_mock), \
         patch.object(people_search, "fs_post", fs_post_mock), \
         patch.object(captcha_solver.settings, "twocaptcha_api_key", ""), \
         patch.object(captcha_solver, "solve_turnstile", solve_mock):
        result = asyncio.run(
            people_search.get_person_detail(
                "https://www.fastpeoplesearch.com/aaron-tom_id_X",
                use_flaresolverr=True,
                use_captcha=True,
            )
        )

    # No paid solve attempt, no re-issue
    assert solve_mock.await_count == 0
    assert fs_post_mock.await_count == 0
    assert result["turnstile_pending"] is True
    assert result["person"] is None  # Challenge HTML has no Person record


def test_get_person_detail_skips_captcha_when_use_captcha_false():
    fs_get_mock = AsyncMock(return_value=(_TURNSTILE_CHALLENGE_HTML, "ok"))
    solve_mock = AsyncMock()

    with patch.object(people_search, "fs_get", fs_get_mock), \
         patch.object(captcha_solver.settings, "twocaptcha_api_key", "key-xyz"), \
         patch.object(captcha_solver, "solve_turnstile", solve_mock):
        result = asyncio.run(
            people_search.get_person_detail(
                "https://www.fastpeoplesearch.com/aaron-tom_id_Y",
                use_flaresolverr=True,
                use_captcha=False,
            )
        )

    assert solve_mock.await_count == 0
    assert result["turnstile_pending"] is True


def test_get_person_detail_no_challenge_parses_directly():
    fs_get_mock = AsyncMock(return_value=(_REAL_DETAIL_HTML, "ok"))
    fs_post_mock = AsyncMock()

    with patch.object(people_search, "fs_get", fs_get_mock), \
         patch.object(people_search, "fs_post", fs_post_mock):
        result = asyncio.run(
            people_search.get_person_detail(
                "https://www.fastpeoplesearch.com/aaron-tom_id_Z",
                use_flaresolverr=True,
                use_captcha=True,
            )
        )

    assert fs_post_mock.await_count == 0  # No re-issue when no challenge
    assert result["turnstile_pending"] is False
    assert result["person"]["name"] == "Aaron Tom"


def test_extract_turnstile_sitekey_picks_up_cf_turnstile_div():
    key = people_search._extract_turnstile_sitekey(_TURNSTILE_CHALLENGE_HTML)
    assert key == "0x4AAAAAAATESTKEY"


def test_extract_turnstile_sitekey_returns_none_on_clean_html():
    assert people_search._extract_turnstile_sitekey(_REAL_DETAIL_HTML) is None


# --- _build_address_url --------------------------------------------------


def test_build_address_url_formats_correctly():
    url = people_search._build_address_url("123 Main St", "Columbus", "OH")
    assert url == "https://www.fastpeoplesearch.com/address/123-main-st_columbus-oh"


def test_build_address_url_lowercases_and_hyphenates():
    url = people_search._build_address_url("456 Oak Avenue", "Grove City", "Ohio")
    assert url == "https://www.fastpeoplesearch.com/address/456-oak-avenue_grove-city-ohio"


# --- _detect_wall --------------------------------------------------------


_DATADOME_HTML = """<!doctype html>
<html><body>
  <script src="https://geo.captcha-delivery.com/captcha/?initialCid=abc"></script>
  <script src="/js/dd.js"></script>
</body></html>"""

_CF_INTERSTITIAL_HTML = """<!doctype html>
<html><body>
  <h2>Waiting for www.fastpeoplesearch.com to respond...</h2>
</body></html>"""


def test_detect_wall_identifies_datadome():
    wall = people_search._detect_wall(_DATADOME_HTML)
    assert wall is not None
    assert wall["kind"] == "datadome"


def test_detect_wall_identifies_cf_interstitial():
    wall = people_search._detect_wall(_CF_INTERSTITIAL_HTML)
    assert wall is not None
    assert wall["kind"] == "cf_interstitial"


def test_detect_wall_returns_none_on_clean_html():
    assert people_search._detect_wall(_REAL_DETAIL_HTML) is None


def test_detect_wall_returns_none_on_empty():
    assert people_search._detect_wall("") is None


# --- search_people_by_address --------------------------------------------

_ADDRESS_LISTING_HTML = """<!doctype html>
<html><body>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "Person",
  "@id": "https://www.fastpeoplesearch.com/jane-doe_id_X1",
  "url": "https://www.fastpeoplesearch.com/jane-doe_id_X1",
  "name": "Jane Doe",
  "HomeLocation": [{
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "123 Main St",
      "addressLocality": "Columbus",
      "addressRegion": "OH",
      "postalCode": "43215"
    }
  }]
}
</script>
</body></html>"""


def test_search_people_by_address_returns_people_via_flaresolverr():
    fs_get_mock = AsyncMock(return_value=(_ADDRESS_LISTING_HTML, "ok"))
    with patch.object(people_search, "fs_get", fs_get_mock):
        result = asyncio.run(
            people_search.search_people_by_address("123 Main St", "Columbus", "OH")
        )
    assert fs_get_mock.await_count == 1
    called_url = fs_get_mock.await_args.args[0]
    assert called_url == "https://www.fastpeoplesearch.com/address/123-main-st_columbus-oh"
    assert result["wall"] is None
    assert len(result["people"]) == 1
    assert result["people"][0]["name"] == "Jane Doe"
    assert result["people"][0]["addresses"][0]["region"] == "OH"


def test_search_people_by_address_captures_cloudflare_wall():
    fs_get_mock = AsyncMock(side_effect=people_search.CloudflareWallError("blocked", 0))
    with patch.object(people_search, "fs_get", fs_get_mock):
        result = asyncio.run(
            people_search.search_people_by_address("123 Main St", "Columbus", "OH")
        )
    assert result["wall"]["kind"] == "cloudflare_wall"
    assert result["people"] == []


def test_search_people_by_address_captures_datadome():
    fs_get_mock = AsyncMock(return_value=(_DATADOME_HTML, "ok"))
    with patch.object(people_search, "fs_get", fs_get_mock):
        result = asyncio.run(
            people_search.search_people_by_address("123 Main St", "Columbus", "OH")
        )
    assert result["wall"]["kind"] == "datadome"
    assert result["people"] == []


def test_search_people_by_address_turnstile_pending_when_no_captcha_key():
    fs_get_mock = AsyncMock(return_value=(_TURNSTILE_CHALLENGE_HTML, "ok"))
    solve_mock = AsyncMock()
    with patch.object(people_search, "fs_get", fs_get_mock), \
         patch.object(captcha_solver.settings, "twocaptcha_api_key", ""), \
         patch.object(captcha_solver, "solve_turnstile", solve_mock):
        result = asyncio.run(
            people_search.search_people_by_address("123 Main St", "Columbus", "OH",
                                                   use_captcha=True)
        )
    assert result["wall"]["kind"] == "turnstile_pending"
    assert solve_mock.await_count == 0
    assert result["people"] == []


def test_search_people_by_address_solves_turnstile_when_configured():
    fs_get_mock = AsyncMock(return_value=(_TURNSTILE_CHALLENGE_HTML, "ok"))
    fs_post_mock = AsyncMock(return_value=(_ADDRESS_LISTING_HTML, "ok"))
    solve_mock = AsyncMock(return_value="0.TOKEN_XYZ")

    with patch.object(people_search, "fs_get", fs_get_mock), \
         patch.object(people_search, "fs_post", fs_post_mock), \
         patch.object(captcha_solver.settings, "twocaptcha_api_key", "key-abc"), \
         patch.object(captcha_solver, "solve_turnstile", solve_mock):
        result = asyncio.run(
            people_search.search_people_by_address("123 Main St", "Columbus", "OH",
                                                   use_captcha=True)
        )

    assert solve_mock.await_count == 1
    assert result["wall"] is None
    assert len(result["people"]) == 1
    assert result["people"][0]["name"] == "Jane Doe"


def test_search_people_by_address_falls_back_to_direct_on_flaresolverr_error():
    from app.services.scraper.flaresolverr import FlareSolverrError
    fs_get_mock = AsyncMock(side_effect=FlareSolverrError("timeout"))
    direct_mock = AsyncMock(return_value=(_ADDRESS_LISTING_HTML, 200))
    with patch.object(people_search, "fs_get", fs_get_mock), \
         patch.object(people_search, "_fetch_direct", direct_mock):
        result = asyncio.run(
            people_search.search_people_by_address("123 Main St", "Columbus", "OH")
        )
    assert direct_mock.await_count == 1
    assert result["wall"] is None
    assert result["people"][0]["name"] == "Jane Doe"
