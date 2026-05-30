"""
FastPeopleSearch scraper (PHA-795).

Listings: JSON-LD `<script type="application/ld+json">` blocks reliably contain
`@type: Person` records when fetched through FlareSolverr — no captcha needed.

Detail pages: FPS gates them behind a Cloudflare Turnstile widget. FlareSolverr
clears the easy variants; the 'elevated' variant times out and the page either
comes back containing the Turnstile challenge or `fs_get` raises
`CloudflareWallError`. In that case we extract the `data-sitekey`, hand it to
`captcha_solver.solve_turnstile`, and re-POST the URL with the resulting token
in `cf-turnstile-response`.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from app.services.scraper import captcha_solver
from app.services.scraper.flaresolverr import (
    CloudflareWallError,
    FlareSolverrError,
    fs_get,
    fs_post,
)


FPS_BASE = "https://www.fastpeoplesearch.com"
_DIRECT_TIMEOUT = 30.0
_DIRECT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_TURNSTILE_SITEKEY_RE = re.compile(
    r'(?:cf-turnstile[^>]*?data-sitekey|data-sitekey[^>]*?cf-turnstile|'
    r'turnstile[^>]*?sitekey|sitekey["\'\s:=]+)["\']?(0x[A-Za-z0-9_-]{8,})',
    re.IGNORECASE,
)
# DataDome challenge pages include a captcha-delivery.com link or dd.js asset.
_DATADOME_RE = re.compile(
    r'captcha-delivery\.com|/js/dd\.js|datadome\.co/captcha',
    re.IGNORECASE,
)
# CF JS interstitial that leaks through when FlareSolverr doesn't fully clear it.
_CF_INTERSTITIAL_RE = re.compile(
    r'Waiting for (?:www\.)?fastpeoplesearch\.com to respond',
    re.IGNORECASE,
)


def _build_search_url(first: str, last: str, state: Optional[str], city: Optional[str]) -> str:
    """FPS search URL pattern: /name/{first}-{last}_{city}-{state}."""
    first_s = (first or "").strip().lower().replace(" ", "-")
    last_s = (last or "").strip().lower().replace(" ", "-")
    name_part = f"{first_s}-{last_s}".strip("-")
    suffix_bits = []
    if city:
        suffix_bits.append(city.strip().lower().replace(" ", "-"))
    if state:
        suffix_bits.append(state.strip().lower().replace(" ", "-"))
    if suffix_bits:
        return f"{FPS_BASE}/name/{name_part}_{'-'.join(suffix_bits)}"
    return f"{FPS_BASE}/name/{name_part}"


def _build_address_url(street: str, city: str, state: str) -> str:
    """FPS address search URL pattern: /address/{street-slug}_{city}-{state}."""
    street_s = (street or "").strip().lower().replace(" ", "-")
    city_s = (city or "").strip().lower().replace(" ", "-")
    state_s = (state or "").strip().lower().replace(" ", "-")
    return f"{FPS_BASE}/address/{street_s}_{city_s}-{state_s}"


def _detect_wall(html: str) -> Optional[dict]:
    """Detect known anti-bot walls in HTML. Returns a wall descriptor or None."""
    if not html:
        return None
    if _DATADOME_RE.search(html):
        return {"kind": "datadome", "detail": "DataDome challenge detected; stop to avoid proxy burn"}
    if _CF_INTERSTITIAL_RE.search(html):
        return {"kind": "cf_interstitial", "detail": "Cloudflare JS interstitial not cleared by FlareSolverr"}
    return None


def _iter_json_ld_objects(html: str):
    """Yield each parsed JSON-LD object found in the HTML (skips parse failures)."""
    for match in _JSON_LD_RE.finditer(html or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            # FPS pages have a couple of decorative LD blocks that aren't valid
            # JSON (trailing commas etc.); skip them rather than crashing.
            continue


def _walk_person_records(obj):
    """Recurse JSON-LD object and yield every `@type: Person` dict."""
    if isinstance(obj, dict):
        type_field = obj.get("@type")
        types = type_field if isinstance(type_field, list) else [type_field]
        if "Person" in types:
            yield obj
        for value in obj.values():
            yield from _walk_person_records(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_person_records(item)


def _flatten_address(place: dict) -> dict:
    address = place.get("address") or {}
    return {
        "description": place.get("description"),
        "locality": address.get("addressLocality"),
        "region": address.get("addressRegion"),
        "postal_code": address.get("postalCode"),
        "street": address.get("streetAddress"),
    }


def _normalize_person(record: dict) -> dict:
    """Reduce a JSON-LD Person record to the flat dict the rest of the app expects."""
    home_locations = record.get("HomeLocation") or []
    if isinstance(home_locations, dict):
        home_locations = [home_locations]
    relatives = []
    for related in record.get("relatedTo") or []:
        if isinstance(related, dict) and related.get("name"):
            relatives.append(related["name"])
    return {
        "name": record.get("name"),
        "url": record.get("url") or record.get("@id"),
        "addresses": [_flatten_address(p) for p in home_locations if isinstance(p, dict)],
        "relatives": relatives,
        "source": "fastpeoplesearch",
    }


def parse_listing_people(html: str) -> list[dict]:
    """Extract every Person record from a FastPeopleSearch listing page."""
    people: list[dict] = []
    seen_ids: set[str] = set()
    for obj in _iter_json_ld_objects(html):
        for person in _walk_person_records(obj):
            pid = person.get("@id") or person.get("url") or person.get("name")
            if pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)
            people.append(_normalize_person(person))
    return people


def _extract_turnstile_sitekey(html: str) -> Optional[str]:
    """Look for a Cloudflare Turnstile sitekey on a challenge page."""
    if not html:
        return None
    if "turnstile" not in html.lower() and "0x4" not in html and "0x5" not in html:
        return None
    match = _TURNSTILE_SITEKEY_RE.search(html)
    if match:
        return match.group(1)
    return None


async def _fetch_direct(url: str, *, post_data: Optional[dict] = None) -> tuple[str, int]:
    async with httpx.AsyncClient(timeout=_DIRECT_TIMEOUT, follow_redirects=True) as client:
        if post_data is not None:
            resp = await client.post(url, data=post_data, headers=_DIRECT_HEADERS)
        else:
            resp = await client.get(url, headers=_DIRECT_HEADERS)
        return resp.text, resp.status_code


async def search_people(
    first: str,
    last: str,
    state: Optional[str] = None,
    city: Optional[str] = None,
    *,
    use_flaresolverr: bool = True,
) -> list[dict]:
    """
    Search FastPeopleSearch by name and return normalized Person records.

    Args:
        first: first name
        last: last name
        state: optional state filter (full name or 2-letter abbreviation)
        city: optional city filter
        use_flaresolverr: route the listing request through FlareSolverr (default)
    """
    url = _build_search_url(first, last, state, city)
    if use_flaresolverr:
        try:
            html, _ = await fs_get(url)
        except (CloudflareWallError, FlareSolverrError):
            html, _ = await _fetch_direct(url)
    else:
        html, _ = await _fetch_direct(url)
    return parse_listing_people(html)


async def search_people_by_address(
    street: str,
    city: str,
    state: str,
    *,
    use_flaresolverr: bool = True,
    use_captcha: bool = True,
) -> dict:
    """
    Search FastPeopleSearch by address and return normalized Person records.

    Returns a dict with:
      - url: the URL fetched
      - people: list of normalized Person records (empty when a wall blocked parsing)
      - wall: None or a descriptor dict if an anti-bot wall was encountered

    Wall descriptor kinds:
      "cloudflare_wall"   — FlareSolverr raised CloudflareWallError (JS challenge not cleared)
      "turnstile_pending" — Turnstile challenge present; captcha solve not attempted
      "turnstile_unsolved"— Turnstile challenge remained after 2Captcha solve attempt
      "datadome"          — DataDome challenge detected; stop, do not burn proxy budget
      "cf_interstitial"   — Cloudflare JS interstitial leaked through FlareSolverr
    """
    url = _build_address_url(street, city, state)
    html = ""
    wall: Optional[dict] = None

    if use_flaresolverr:
        try:
            html, _ = await fs_get(url)
        except CloudflareWallError as exc:
            wall = {"kind": "cloudflare_wall", "detail": str(exc)}
        except FlareSolverrError:
            html, _ = await _fetch_direct(url)
    else:
        html, _ = await _fetch_direct(url)

    # Hard-wall detection: DataDome or leaking CF interstitial — stop immediately.
    if wall is None:
        wall = _detect_wall(html)

    # Turnstile challenge within a page that FlareSolverr partially loaded.
    if wall is None:
        sitekey = _extract_turnstile_sitekey(html)
        if sitekey is not None:
            if use_captcha and captcha_solver.is_configured():
                token = await captcha_solver.solve_turnstile(sitekey, url)
                html, _ = await _fetch_detail(
                    url,
                    use_flaresolverr=use_flaresolverr,
                    post_data={"cf-turnstile-response": token},
                )
                if _extract_turnstile_sitekey(html) is not None:
                    wall = {"kind": "turnstile_unsolved", "sitekey": sitekey}
            else:
                wall = {"kind": "turnstile_pending", "sitekey": sitekey}

    people = parse_listing_people(html) if html and wall is None else []
    return {"url": url, "people": people, "wall": wall}


async def get_person_detail(
    detail_url: str,
    *,
    use_flaresolverr: bool = True,
    use_captcha: bool = True,
) -> dict:
    """
    Fetch a FastPeopleSearch detail page and parse the Person record.

    If the response is a Turnstile challenge page and `use_captcha` is set,
    solve via 2Captcha and re-issue the request. When 2Captcha isn't configured
    the function returns whatever HTML was fetched with `turnstile_pending: True`
    rather than raising — callers can fall back to manual review.
    """
    html, status = await _fetch_detail(detail_url, use_flaresolverr=use_flaresolverr)

    sitekey = _extract_turnstile_sitekey(html)
    turnstile_pending = False
    if sitekey is not None:
        if not (use_captcha and captcha_solver.is_configured()):
            turnstile_pending = True
        else:
            token = await captcha_solver.solve_turnstile(sitekey, detail_url)
            html, status = await _fetch_detail(
                detail_url,
                use_flaresolverr=use_flaresolverr,
                post_data={"cf-turnstile-response": token},
            )
            # If the re-issue still shows a Turnstile widget, surface that.
            if _extract_turnstile_sitekey(html) is not None:
                turnstile_pending = True

    people = parse_listing_people(html)
    person = people[0] if people else None
    return {
        "url": detail_url,
        "status": status,
        "person": person,
        "turnstile_pending": turnstile_pending,
        "html": html,
    }


async def _fetch_detail(
    url: str,
    *,
    use_flaresolverr: bool,
    post_data: Optional[dict] = None,
) -> tuple[str, int]:
    """Single fetch attempt; swallows FlareSolverr challenges so we can inspect the body."""
    if use_flaresolverr:
        try:
            if post_data is not None:
                return await fs_post(url, post_data=post_data)
            return await fs_get(url)
        except CloudflareWallError as exc:
            # FlareSolverr saw a challenge — fall through so the caller can
            # detect the Turnstile sitekey and solve it.
            return "", getattr(exc, "http_status", 0) or 0
        except FlareSolverrError:
            return await _fetch_direct(url, post_data=post_data)
    return await _fetch_direct(url, post_data=post_data)
