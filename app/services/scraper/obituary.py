"""
Obituary scrapers — Legacy.com, Columbus Dispatch, and Find a Grave (PHA-1349).

Ports the proven DataDome solve path from PHA-820 (`legacy_com.py` /
`dispatch.py`) into one module and adds Find a Grave, all three sharing the
same fetch-escalation strategy:

  1. Direct httpx (+ DataDome 2Captcha solve via the shared `datadome` core
     when DATADOME_SOLVE_PROXY is set). Fast and clean for routes that aren't
     currently walled.
  2. On any block (DataDome challenge with no proxy configured, or a generic
     Cloudflare/Imperva interstitial), escalate to a crawl4ai headless render
     — its residential-ish egress clears walls a bare container GET hits, no
     paid proxy needed.
  3. If the render is also blocked, surface the original wall so callers fall
     back to another source instead of seeing fake-empty data.

Sources:

  - Legacy.com is a Next.js app whose obituary data is rendered into JSON-LD:
      /obituaries/search?firstName=&lastName=&state=
          -> SearchResultsPage -> ItemList of {position, name, url}.
      /person/<Name>-<id>
          -> CreativeWork whose `about` is a Person (name/dates/image) plus
             the memorial `text`; some routes instead render a standalone
             Person + NewsArticle.articleBody (affiliate shape).
    DataDome guards the search/person routes path-dependently (observed live
    2026-06-13): the homepage is clean, the data routes intermittently 403
    with a `var dd={...}` challenge.

  - dispatch.com/obituaries is a Gannett shell that embeds Legacy.com: the
    real, data-bearing feed for The Columbus Dispatch lives on Legacy's
    affiliate path `https://www.legacy.com/obituaries/dispatch/`, which
    renders the same JSON-LD `legacy_parse_search_results` already
    understands. Detail pages are normal Legacy pages, so
    `legacy_parse_obituary_detail` parses them too. `dispatch_*` is therefore
    a thin Dispatch-flavored facade over the Legacy parsers/fetch.

  - Find a Grave (findagrave.com) is plain server-rendered HTML (no JSON-LD):
      /memorial/search?firstname=&lastname=&location=
          -> rows of `div.memorial-item` (name, birth/death dates, cemetery,
             location).
      /memorial/<id>/<slug>
          -> `#bio-name`/`#birthDateLabel`/`#deathDateLabel`/etc. for the
             core record, `#fullBio` for the memorial text, and a
             `#family-grid` of relation groups (Parents/Spouse/Children/
             Siblings/...), each a `<ul class="member-family">` of linked
             Person `<li>`s — genealogy relationship facts, verified live
             2026-07-11. Findagrave has no DataDome stub; a bare container GET
             hits a generic Cloudflare "Just a moment..." interstitial, so it
             rides the same `_resolve_html` escalation without ever reaching
             the 2Captcha branch.
"""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from app.services.scraper import datadome
from app.services.scraper.captcha_solver import CaptchaSolverError
from app.services.scraper.crawl4ai import crawl4ai_fetch_html


# ---------------------------------------------------------------------------
# Shared fetch escalation (Legacy.com, Dispatch, Find a Grave all ride this)
# ---------------------------------------------------------------------------

async def _resolve_html(url: str) -> tuple[str, Optional[dict]]:
    """Return (html, wall) for an obituary-source URL.

    See the module docstring for the fast-path / crawl4ai-render / typed-wall
    strategy this implements.
    """
    try:
        html, _status = await datadome.fetch_with_solve(url)
        return html, None
    except (datadome.DataDomeError, CaptchaSolverError) as exc:
        wall = datadome.wall_for(exc)

    rendered, rstatus = await crawl4ai_fetch_html(url)
    if (
        rendered
        and not datadome.is_challenge(rendered, rstatus)
        and not datadome.is_generic_block(rendered, rstatus)
    ):
        return rendered, None
    return "", wall


# ===========================================================================
# Legacy.com
# ===========================================================================

LEGACY_BASE = "https://www.legacy.com"

_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_OBIT_SUFFIX_RE = re.compile(r"\s+Obituary\s*$", re.IGNORECASE)


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


def legacy_parse_search_results(html: str) -> list[dict]:
    """Extract obituary listing rows from a Legacy/dispatch search page.

    Pulls the SearchResultsPage -> ItemList -> itemListElement entries. Each
    row is normalized to {name, url, position, source}.
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


def legacy_parse_obituary_detail(html: str) -> Optional[dict]:
    """Extract a single obituary from a Legacy/dispatch detail page.

    Handles both observed JSON-LD shapes:
      - /person/<Name>-<id>      -> CreativeWork whose `about` is a Person,
                                    with the memorial blurb in
                                    CreativeWork.text.
      - affiliate /name/<slug>   -> a standalone Person plus a NewsArticle
                                    whose `articleBody` is the full obituary
                                    text and a `deathPlace` with
                                    locality/region.

    Returns {name, given_name, family_name, birth_date, death_date,
    death_place, image, text, url, source} or None when no Person record is
    present.
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
    # article author/publisher Person, which has neither. Fall back to a
    # Person that is the `about` of a CreativeWork (the /person memorial
    # shape).
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


def legacy_build_search_url(
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


async def legacy_search(
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
    datadome_solve_limit, datadome_challenge, upstream_blocked,
    captcha_not_configured, captcha_error. When `wall` is set the path is
    inert (proxy unconfigured) or could not solve; callers should fall back
    to another source rather than retry.
    """
    url = legacy_build_search_url(first, last, state, base=base)
    html, wall = await _resolve_html(url)

    results = legacy_parse_search_results(html) if html and wall is None else []
    for r in results:
        r["source"] = source
    return {"url": url, "results": results, "wall": wall, "source": source}


async def legacy_get_obituary(detail_url: str, *, source: str = "legacy.com") -> dict:
    """Fetch a Legacy.com /person/<...> obituary and parse it.

    Returns::

        {"url": str, "obituary": dict | None, "wall": None | dict, "source": str}
    """
    html, wall = await _resolve_html(detail_url)

    obit = legacy_parse_obituary_detail(html) if html and wall is None else None
    if obit is not None:
        obit["source"] = source
    return {"url": detail_url, "obituary": obit, "wall": wall, "source": source}


# ===========================================================================
# Columbus Dispatch — thin facade over the Legacy affiliate feed
# ===========================================================================
#
# dispatch.com/obituaries is a Gannett cobrand shell that embeds Legacy.com:
# the real, data-bearing obituary feed for The Columbus Dispatch lives on
# Legacy's affiliate path https://www.legacy.com/obituaries/dispatch/, which
# renders the same JSON-LD ItemList `legacy_parse_search_results` already
# understands (verified live 2026-06-13 — current Columbus obituaries: Neil
# Gant, Ronald Kish, Mark Monsarrat, ...). Detail pages are normal Legacy
# /person|/obituaries pages, so `legacy_parse_obituary_detail` parses them
# too.

DISPATCH_SOURCE = "dispatch.com"
# Legacy affiliate feed that backs dispatch.com/obituaries.
DISPATCH_AFFILIATE_URL = "https://www.legacy.com/obituaries/dispatch/"
# Dispatch is a Columbus, OH paper — scope name search to its market.
DISPATCH_STATE = "Ohio"


async def dispatch_browse_recent() -> dict:
    """Fetch the recent Columbus Dispatch obituary feed.

    Returns::

        {
          "url": str,
          "results": list[dict],   # normalized listing rows (source="dispatch.com")
          "wall": None | dict,
          "source": "dispatch.com",
        }
    """
    url = DISPATCH_AFFILIATE_URL
    html, wall = await _resolve_html(url)

    results = legacy_parse_search_results(html) if html and wall is None else []
    for r in results:
        r["source"] = DISPATCH_SOURCE
    return {"url": url, "results": results, "wall": wall, "source": DISPATCH_SOURCE}


async def dispatch_search(first: Optional[str] = None, last: Optional[str] = None) -> dict:
    """Search obituaries for the Dispatch market (Ohio) by name.

    Delegates to Legacy's name index scoped to Ohio and re-tags the source;
    the affiliate path itself exposes no standalone name-search route.
    """
    return await legacy_search(first, last, DISPATCH_STATE, source=DISPATCH_SOURCE)


async def dispatch_get_obituary(detail_url: str) -> dict:
    """Fetch and parse a single Dispatch/Legacy obituary detail page."""
    return await legacy_get_obituary(detail_url, source=DISPATCH_SOURCE)


# ===========================================================================
# Find a Grave
# ===========================================================================

FINDAGRAVE_BASE = "https://www.findagrave.com"
FINDAGRAVE_SOURCE = "findagrave.com"

_MEMORIAL_ID_RE = re.compile(r"^/memorial/(\d+)/")


def findagrave_build_search_url(
    first: Optional[str] = None,
    last: Optional[str] = None,
    location: Optional[str] = None,
    *,
    base: str = FINDAGRAVE_BASE,
) -> str:
    """Find a Grave memorial search URL.

    `location` is a freeform place name (e.g. "Ohio"); Find a Grave matches it
    loosely without a resolved `locationId`.
    """
    params = []
    if first and first.strip():
        params.append(f"firstname={quote(first.strip())}")
    if last and last.strip():
        params.append(f"lastname={quote(last.strip())}")
    if location and location.strip():
        params.append(f"location={quote(location.strip())}")
    query = "&".join(params)
    return f"{base}/memorial/search" + (f"?{query}" if query else "")


def _stripped_text(el) -> Optional[str]:
    """Flatten an element's text to one line, collapsing internal whitespace.

    Find a Grave renders multi-part fields (e.g. a cemetery address) as a
    single text node broken across several indented lines rather than
    separate child elements, so a plain `strip()` leaves the interior
    newlines in place; this collapses them into the field's own commas.
    """
    if el is None:
        return None
    text = re.sub(r"\s+", " ", el.get_text()).strip()
    return text or None


def findagrave_parse_search_results(html: str) -> list[dict]:
    """Extract memorial rows from a Find a Grave search results page.

    Each row is normalized to {name, memorial_id, url, birth_date, death_date,
    cemetery, cemetery_url, location, source}.
    """
    soup = BeautifulSoup(html or "", "lxml")
    rows: list[dict] = []
    for item in soup.select("div.memorial-item"):
        link = item.select_one("a[href^='/memorial/']")
        if not link:
            continue
        href = link.get("href", "")
        id_match = _MEMORIAL_ID_RE.match(href)
        if not id_match:
            continue

        name = _stripped_text(item.select_one("h2.name-grave i"))

        birth_date = death_date = None
        dates_el = item.select_one("b.birthDeathDates")
        if dates_el:
            parts = dates_el.get_text(strip=True).split("–")  # en dash
            if len(parts) == 2:
                birth_date, death_date = parts[0].strip(), parts[1].strip()

        cemetery_form = item.select_one("form[action^='/cemetery/']")
        cemetery = None
        cemetery_url = None
        if cemetery_form:
            cemetery = _stripped_text(cemetery_form.select_one("button"))
            cemetery_url = urljoin(FINDAGRAVE_BASE, cemetery_form.get("action", ""))

        location = _stripped_text(item.select_one("p.addr-cemet"))

        rows.append(
            {
                "name": name,
                "memorial_id": id_match.group(1),
                "url": urljoin(FINDAGRAVE_BASE, href),
                "birth_date": birth_date,
                "death_date": death_date,
                "cemetery": cemetery,
                "cemetery_url": cemetery_url,
                "location": location,
                "source": FINDAGRAVE_SOURCE,
            }
        )
    return rows


def findagrave_parse_memorial(html: str) -> Optional[dict]:
    """Extract a single Find a Grave memorial: record, bio, and family links.

    Returns::

        {
          "memorial_id": str | None,
          "name": str | None,
          "birth_date": str | None,
          "birth_place": str | None,
          "death_date": str | None,
          "death_place": str | None,
          "cemetery": {"name", "url", "city", "county", "state", "country"} | None,
          "bio": str | None,
          "family": {relation_slug: [{"name", "memorial_id", "url",
                                       "birth_year", "death_year"}]},
          "source": "findagrave.com",
        }

    or None when the page has no `#bio-name` record (walled/empty page).
    Family relation groups (parents, spouses, children, siblings, ...) come
    from the `#family-grid` panel, keyed by Find a Grave's own relation label
    lowercased with spaces turned to underscores (e.g. "half_siblings").
    """
    soup = BeautifulSoup(html or "", "lxml")
    name_el = soup.select_one("#bio-name")
    if name_el is None:
        return None
    name = next(name_el.stripped_strings, None)

    birth_date = _stripped_text(soup.select_one("#birthDateLabel"))
    birth_place = _stripped_text(soup.select_one("#birthLocationLabel"))
    death_date = _stripped_text(soup.select_one("#deathDateLabel"))
    death_place = _stripped_text(soup.select_one("#deathLocationLabel"))

    cemetery = None
    cemetery_name_el = soup.select_one("#cemeteryNameLabel")
    if cemetery_name_el:
        cemetery_link = cemetery_name_el.find_parent("a")
        cemetery = {
            "name": cemetery_name_el.get_text(strip=True),
            "url": urljoin(FINDAGRAVE_BASE, cemetery_link["href"])
            if cemetery_link and cemetery_link.get("href") else None,
            "city": _stripped_text(soup.select_one("#cemeteryCityName")),
            "county": _stripped_text(soup.select_one("#cemeteryCountyName")),
            "state": _stripped_text(soup.select_one("#cemeteryStateName")),
            "country": _stripped_text(soup.select_one("#cemeteryCountryName")),
        }

    bio_el = soup.select_one("#fullBio") or soup.select_one("#partBio")
    bio = _stripped_text(bio_el)

    family: dict[str, list[dict]] = {}
    for group in soup.select("#family-grid > div"):
        label_el = group.select_one("b.label-relation")
        if not label_el:
            continue
        relation = label_el.get_text(strip=True)
        slug = relation.lower().replace(" ", "_").replace("-", "_")
        members = []
        for li in group.select("li[itemtype='https://schema.org/Person']"):
            a = li.select_one("a[href^='/memorial/']")
            if not a:
                continue
            href = a.get("href", "")
            id_match = _MEMORIAL_ID_RE.match(href)
            name_el = li.select_one("h3[itemprop='name']")
            member_name = (
                " ".join(name_el.get_text(" ", strip=True).split())
                if name_el else None
            )
            members.append(
                {
                    "name": member_name,
                    "memorial_id": id_match.group(1) if id_match else None,
                    "url": urljoin(FINDAGRAVE_BASE, href),
                    "birth_year": _stripped_text(li.select_one("[itemprop='birthDate']")),
                    "death_year": _stripped_text(li.select_one("[itemprop='deathDate']")),
                }
            )
        if members:
            family[slug] = members

    canonical = soup.select_one("link[rel='canonical']")
    memorial_id = None
    if canonical and canonical.get("href"):
        id_match = re.search(r"/memorial/(\d+)/", canonical["href"])
        memorial_id = id_match.group(1) if id_match else None

    return {
        "memorial_id": memorial_id,
        "name": name,
        "birth_date": birth_date,
        "birth_place": birth_place,
        "death_date": death_date,
        "death_place": death_place,
        "cemetery": cemetery,
        "bio": bio,
        "family": family,
        "source": FINDAGRAVE_SOURCE,
    }


async def findagrave_search(
    first: Optional[str] = None,
    last: Optional[str] = None,
    location: Optional[str] = None,
) -> dict:
    """Search Find a Grave memorials by name (and optional freeform location).

    Returns::

        {"url": str, "results": list[dict], "wall": None | dict, "source": "findagrave.com"}
    """
    url = findagrave_build_search_url(first, last, location)
    html, wall = await _resolve_html(url)

    results = findagrave_parse_search_results(html) if html and wall is None else []
    return {"url": url, "results": results, "wall": wall, "source": FINDAGRAVE_SOURCE}


async def findagrave_get_memorial(url: str) -> dict:
    """Fetch and parse a single Find a Grave /memorial/<id>/<slug> page.

    Returns::

        {"url": str, "memorial": dict | None, "wall": None | dict, "source": "findagrave.com"}
    """
    html, wall = await _resolve_html(url)

    memorial = findagrave_parse_memorial(html) if html and wall is None else None
    if memorial is not None and memorial.get("memorial_id") is None:
        id_match = re.search(r"/memorial/(\d+)/", url)
        if id_match:
            memorial["memorial_id"] = id_match.group(1)
    return {"url": url, "memorial": memorial, "wall": wall, "source": FINDAGRAVE_SOURCE}
