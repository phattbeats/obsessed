"""
Legacy.com obituary scraper — DataDome solve path (PHA-820).

Legacy.com is a Next.js app whose obituary data is rendered into JSON-LD on the
page (no separate JSON API token needed):

  - /obituaries/search?firstName=&lastName=&state=
        -> <script type="application/ld+json"> SearchResultsPage whose
           mainEntity is an ItemList of {position, name, url} obituary links.
  - /person/<Name>-<id>
        -> <script type="application/ld+json"> CreativeWork whose `about` is a
           Person with name/givenName/familyName/birthDate/deathDate/image, plus
           the memorial `text`.

DataDome guards these data routes path-dependently: the marketing homepage and a
warm session return clean HTML, but the search / person routes intermittently
answer 403 with a `var dd={...}` + geo.captcha-delivery.com challenge (observed
live 2026-06-13). When that happens we route through the shared solve core
(`datadome.fetch_with_solve`), which is inert unless DATADOME_SOLVE_PROXY is set.

dispatch.com obituaries ride this exact same Legacy platform; see `dispatch.py`,
which reuses the parsers and fetch here against the dispatch.com host.
"""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import quote

from app.services.scraper import datadome
from app.services.scraper.captcha_solver import CaptchaSolverError
from app.services.scraper.crawl4ai import crawl4ai_fetch_html


LEGACY_BASE = "https://www.legacy.com"

_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_OBIT_SUFFIX_RE = re.compile(r"\s+Obituary\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# JSON-LD parsing
# ---------------------------------------------------------------------------

def _iter_json_ld(html: str):
    for m in _JSON_LD_RE.finditer(html or ""):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def _clean_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return name
    return _OBIT_SUFFIX_RE.sub("", name).strip() or None


def parse_search_results(html: str) -> list[dict]:
    """Extract obituary listing rows from a Legacy/dispatch search page.

    Pulls the SearchResultsPage -> ItemList -> itemListElement entries. Each row
    is normalized to {name, url, position, source}.
    """
    results: list[dict] = []
    seen: set[str] = set()
    for obj in _iter_json_ld(html):
        if not isinstance(obj, dict):
            continue
        main = obj.get("mainEntity")
        if not (isinstance(main, dict) and main.get("@type") == "ItemList"):
            continue
        for item in main.get("itemListElement") or []:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("@id")
            if not url or url in seen:
                continue
            seen.add(url)
            results.append(
                {
                    "name": _clean_name(item.get("name")),
                    "url": url,
                    "position": item.get("position"),
                    "source": "legacy.com",
                }
            )
    return results


def _types(obj: dict) -> list:
    t = obj.get("@type")
    return t if isinstance(t, list) else [t]


def _walk_dicts(obj):
    """Yield every dict nested anywhere within a JSON-LD object."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_dicts(item)


def _place_locality_region(place: dict) -> tuple[Optional[str], Optional[str]]:
    addr = place.get("address") if isinstance(place, dict) else None
    if isinstance(addr, dict):
        return addr.get("addressLocality"), addr.get("addressRegion")
    return None, None


def parse_obituary_detail(html: str) -> Optional[dict]:
    """Extract a single obituary from a Legacy/dispatch detail page.

    Handles both observed JSON-LD shapes:
      - /person/<Name>-<id>      -> CreativeWork whose `about` is a Person, with
                                    the memorial blurb in CreativeWork.text.
      - affiliate /name/<slug>   -> a standalone Person plus a NewsArticle whose
                                    `articleBody` is the full obituary text and a
                                    `deathPlace` with locality/region.

    Returns {name, given_name, family_name, birth_date, death_date, death_place,
    image, text, url, source} or None when no Person record is present.
    """
    persons: list[dict] = []
    about_persons: list[dict] = []
    article_text: Optional[str] = None
    creativework_text: Optional[str] = None
    image = None
    url = None
    for obj in _iter_json_ld(html):
        for node in _walk_dicts(obj):
            types = _types(node)
            if "Person" in types:
                persons.append(node)
            if article_text is None and ("NewsArticle" in types or "Article" in types):
                article_text = node.get("articleBody")
            if "CreativeWork" in types:
                creativework_text = creativework_text or node.get("text")
                url = url or node.get("url") or node.get("mainEntityOfPage")
                image = image or node.get("image")
                about = node.get("about")
                if isinstance(about, dict) and "Person" in _types(about):
                    about_persons.append(about)

    # The obituary subject is the Person carrying birth/death dates — not an
    # article author/publisher Person, which has neither. Fall back to a Person
    # that is the `about` of a CreativeWork (the /person memorial shape).
    person = next(
        (p for p in persons if p.get("deathDate") or p.get("birthDate")),
        about_persons[0] if about_persons else None,
    )
    if person is None:
        return None

    image = image or person.get("image")
    locality, region = _place_locality_region(person.get("deathPlace") or {})
    return {
        "name": person.get("name"),
        "given_name": person.get("givenName"),
        "family_name": person.get("familyName"),
        "birth_date": person.get("birthDate"),
        "death_date": person.get("deathDate"),
        "death_place": {"locality": locality, "region": region}
        if (locality or region) else None,
        "image": image,
        "text": article_text or creativework_text or person.get("description"),
        "url": url or person.get("url"),
        "source": "legacy.com",
    }


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

def build_search_url(
    first: Optional[str],
    last: Optional[str],
    state: Optional[str] = None,
    base: str = LEGACY_BASE,
) -> str:
    """Legacy search URL: /obituaries/search?firstName=&lastName=&state=."""
    params = []
    if first and first.strip():
        params.append(f"firstName={quote(first.strip())}")
    if last and last.strip():
        params.append(f"lastName={quote(last.strip())}")
    if state and state.strip():
        params.append(f"state={quote(state.strip())}")
    query = "&".join(params)
    return f"{base}/obituaries/search" + (f"?{query}" if query else "")


# ---------------------------------------------------------------------------
# Fetch orchestration
# ---------------------------------------------------------------------------

async def _resolve_html(url: str) -> tuple[str, Optional[dict]]:
    """Return (html, wall) for a Legacy/dispatch URL.

    Strategy — fast path first, free standalone bypass second:
      1. Direct httpx (+ DataDome 2Captcha solve when DATADOME_SOLVE_PROXY is set).
         This is fast and clean for routes that aren't currently walled.
      2. On any block (DataDome with no proxy, Cloudflare interstitial, 4xx/5xx),
         render through crawl4ai's headless browser — its residential-ish egress
         clears the walls that a bare container GET hits, no paid proxy needed.
      3. If the render is also a challenge/empty, surface the original wall so the
         caller falls back to another source instead of seeing fake-empty data.
    """
    try:
        html, status = await datadome.fetch_with_solve(url)
        return html, None
    except (datadome.DataDomeError, CaptchaSolverError) as exc:
        wall = datadome.wall_for(exc)

    # Escalate to a rendered fetch.
    rendered, rstatus = await crawl4ai_fetch_html(url)
    if (
        rendered
        and not datadome.is_challenge(rendered, rstatus)
        and not datadome.is_generic_block(rendered, rstatus)
    ):
        return rendered, None
    return "", wall


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_obituaries(
    first: Optional[str] = None,
    last: Optional[str] = None,
    state: Optional[str] = None,
    *,
    base: str = LEGACY_BASE,
    source: str = "legacy.com",
) -> dict:
    """Search Legacy.com obituaries by name (and optional state).

    Returns::

        {
          "url": str,
          "results": list[dict],   # normalized listing rows
          "wall": None | dict,     # present when a challenge couldn't be solved
          "source": str,
        }

    Wall kinds mirror the shared solve core: datadome_proxy_missing,
    datadome_solve_limit, datadome_challenge, captcha_not_configured, captcha_error.
    When `wall` is set the path is inert (proxy unconfigured) or could not solve;
    callers should fall back to another source rather than retry.
    """
    url = build_search_url(first, last, state, base=base)
    html, wall = await _resolve_html(url)

    results = parse_search_results(html) if html and wall is None else []
    for r in results:
        r["source"] = source
    return {"url": url, "results": results, "wall": wall, "source": source}


async def get_obituary(
    detail_url: str,
    *,
    source: str = "legacy.com",
) -> dict:
    """Fetch a Legacy.com /person/<...> obituary and parse it.

    Returns::

        {"url": str, "obituary": dict | None, "wall": None | dict, "source": str}
    """
    html, wall = await _resolve_html(detail_url)

    obit = parse_obituary_detail(html) if html and wall is None else None
    if obit is not None:
        obit["source"] = source
    return {"url": detail_url, "obituary": obit, "wall": wall, "source": source}
