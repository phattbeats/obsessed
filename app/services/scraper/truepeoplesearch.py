"""
TruePeopleSearch scraper — DataDome solve path (PHA-820, Option 1).

DataDome is path-dependent on TPS:
  - Homepage /          → 200, clean (no cookie needed)
  - /results?name=...   → 403 with DD challenge (var dd={...} JS + captcha iframe)
  - /find/person/<id>   → 403 with same DD challenge

Solve flow:
  1. Fetch results/detail URL via httpx from the obsessed container
     (egresses from phattvip → same Breezeline residential IP as browserless)
  2. Detect DataDome 403 challenge via body markers
  3. Extract dd params (cid/hsh/host/t/s) from the inline dd={} JS
  4. Check 12-hour in-process cookie cache — skip solve if valid entry exists
  5. Call captcha_solver.solve_datadome(captcha_url, page_url, proxy=…)
     The proxy must also egress from the same residential IP so DataDome's cid
     binding is satisfied. DATADOME_SOLVE_PROXY carries the forward-proxy address.
  6. Set solved datadome cookie in request headers, re-fetch the page
  7. Parse JSON-LD Person records from the result HTML

Requires two env vars to be set (inert when absent):
  TWOCAPTCHA_API_KEY       — solver API key
  DATADOME_SOLVE_PROXY     — forward proxy that egresses from residential IP,
                             format: USER:PASS@HOST:PORT  (HTTP proxy)

Cost guardrail:
  datadome_max_solves_per_run (DATADOME_MAX_SOLVES_PER_RUN env, default 5) caps
  solver calls per process restart so scrape loops can't drain the 2Captcha balance.
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

import httpx

from app.config import settings
from app.services.scraper import captcha_solver
from app.services.scraper.captcha_solver import CaptchaSolverError, CaptchaSolverNotConfigured


TPS_BASE = "https://www.truepeoplesearch.com"
_REQUEST_TIMEOUT = 30.0
_COOKIE_TTL = 12 * 3600  # 12 hours; DataDome cookies are typically 24h–7d

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.118 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# In-process DataDome cookie cache keyed by domain
_cookie_cache: dict[str, tuple[str, float]] = {}

# Per-run solve counter (resets on process restart)
_solves_this_run: int = 0

# DataDome challenge detection
_DD_CHALLENGE_RE = re.compile(
    r"var\s+dd\s*=\s*\{|geo\.captcha-delivery\.com|datadome\.co/captcha",
    re.IGNORECASE,
)
# Extract the dd={...} inline JS object
_DD_OBJECT_RE = re.compile(r"var\s+dd\s*=\s*\{([^}]+)\}", re.IGNORECASE | re.DOTALL)
# JSON-LD script blocks
_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


class DataDomeProxyNotConfigured(Exception):
    """Raised when DATADOME_SOLVE_PROXY is unset but a DataDome challenge was hit."""


class DataDomeSolveLimitReached(Exception):
    """Raised when per-run solve cap is exhausted — prevents balance drain."""


class DataDomeChallengePresent(Exception):
    """Raised when the page is still a challenge after solving."""


def is_configured() -> bool:
    """Return True when both API key and proxy are set."""
    return captcha_solver.is_configured() and bool(
        (settings.datadome_solve_proxy or "").strip()
    )


def _get_cached_cookie(domain: str) -> Optional[str]:
    entry = _cookie_cache.get(domain)
    if entry is None:
        return None
    value, expires_at = entry
    if time.monotonic() > expires_at:
        del _cookie_cache[domain]
        return None
    return value


def _cache_cookie(domain: str, value: str) -> None:
    _cookie_cache[domain] = (value, time.monotonic() + _COOKIE_TTL)


def _is_dd_challenge(html: str, status: int) -> bool:
    """Return True if the response looks like a DataDome challenge page."""
    if status == 403 and _DD_CHALLENGE_RE.search(html):
        return True
    # Also catch cases where status is 200 but body is the tiny DD stub (<8KB)
    if len(html) < 8192 and _DD_CHALLENGE_RE.search(html):
        return True
    return False


def _extract_dd_params(html: str) -> Optional[dict]:
    """
    Parse the inline dd={cid, hsh, host, t, s, ...} object from the challenge body.
    Handles both single-quoted and double-quoted JS object literals.
    Returns None when the dd object is not found or malformed.
    """
    m = _DD_OBJECT_RE.search(html)
    if not m:
        return None
    body = m.group(0)
    params: dict[str, str] = {}
    # String values: 'key':'val' or "key":"val" or mixed
    for field_m in re.finditer(r'["\'](\w+)["\']\s*:\s*["\']([^"\']*)["\']', body):
        params[field_m.group(1)] = field_m.group(2)
    # Numeric values: 'key':12345 or "key":12345
    for field_m in re.finditer(r'["\'](\w+)["\']\s*:\s*(\d+)', body):
        if field_m.group(1) not in params:
            params[field_m.group(1)] = field_m.group(2)
    return params if ("cid" in params and "hsh" in params) else None


def _build_captcha_url(dd: dict, referer: str) -> str:
    """Build the geo.captcha-delivery.com URL that 2Captcha expects."""
    host = dd.get("host", "geo.captcha-delivery.com")
    cid = dd["cid"]
    hsh = dd["hsh"]
    t = dd.get("t", "fe")
    s = dd.get("s", "0")
    return (
        f"https://{host}/captcha/?initialCid={cid}&hash={hsh}&cid={cid}"
        f"&t={t}&s={s}&referer={referer}"
    )


async def _solve_datadome(captcha_url: str, page_url: str) -> str:
    """
    Solve the DataDome challenge via 2Captcha, applying the solve cap and proxy config.
    Returns the solved datadome cookie value.
    """
    global _solves_this_run
    max_solves = settings.datadome_max_solves_per_run
    if _solves_this_run >= max_solves:
        raise DataDomeSolveLimitReached(
            f"DataDome solve cap reached ({max_solves} per run); "
            "restart the process or raise DATADOME_MAX_SOLVES_PER_RUN"
        )
    proxy = (settings.datadome_solve_proxy or "").strip()
    if not proxy:
        raise DataDomeProxyNotConfigured(
            "DATADOME_SOLVE_PROXY is not configured. "
            "Set it to USER:PASS@HOST:PORT pointing to an HTTP forward proxy "
            "that egresses from the same residential IP as the scraper."
        )
    _solves_this_run += 1
    return await captcha_solver.solve_datadome(
        captcha_url, page_url, proxy=proxy, proxytype="HTTP", user_agent=_UA
    )


async def _fetch(url: str, *, datadome_cookie: Optional[str] = None) -> tuple[str, int]:
    """Fetch a TPS URL, optionally injecting the datadome cookie."""
    headers = dict(_HEADERS)
    cookies: dict[str, str] = {}
    if datadome_cookie:
        cookies["datadome"] = datadome_cookie
    async with httpx.AsyncClient(
        timeout=_REQUEST_TIMEOUT,
        follow_redirects=True,
        headers=headers,
    ) as client:
        resp = await client.get(url, cookies=cookies)
        return resp.text, resp.status_code


async def _fetch_with_datadome_solve(url: str) -> tuple[str, int]:
    """
    Fetch a TPS URL, solving DataDome if encountered.

    Check → cached cookie first → if still challenged, solve → retry.
    Raises DataDomeProxyNotConfigured if proxy is unset and a challenge is hit.
    """
    domain = "truepeoplesearch.com"
    cached = _get_cached_cookie(domain)
    if cached:
        html, status = await _fetch(url, datadome_cookie=cached)
        if not _is_dd_challenge(html, status):
            return html, status
        # Cached cookie expired or invalid — drop it and re-solve
        _cookie_cache.pop(domain, None)

    # First attempt without cookie
    html, status = await _fetch(url)
    if not _is_dd_challenge(html, status):
        return html, status

    # Challenge detected — solve it
    dd_params = _extract_dd_params(html)
    if not dd_params:
        raise DataDomeChallengePresent(
            f"DataDome challenge on {url} but could not parse dd params from body"
        )
    captcha_url = _build_captcha_url(dd_params, url)
    solved_cookie = await _solve_datadome(captcha_url, url)
    _cache_cookie(domain, solved_cookie)

    # Retry with the solved cookie
    html, status = await _fetch(url, datadome_cookie=solved_cookie)
    if _is_dd_challenge(html, status):
        raise DataDomeChallengePresent(
            f"DataDome challenge remained on {url} after solve — "
            "cookie may be IP-mismatch; check that DATADOME_SOLVE_PROXY "
            "egresses from the same IP as this container"
        )
    return html, status


# ---------------------------------------------------------------------------
# JSON-LD parsing (TruePeopleSearch uses same schema as FastPeopleSearch)
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


def _walk_persons(obj):
    if isinstance(obj, dict):
        types = obj.get("@type")
        if isinstance(types, str):
            types = [types]
        if types and "Person" in types:
            yield obj
        for v in obj.values():
            yield from _walk_persons(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_persons(item)


def _flatten_address(place: dict) -> dict:
    addr = place.get("address") or {}
    return {
        "description": place.get("description"),
        "locality": addr.get("addressLocality"),
        "region": addr.get("addressRegion"),
        "postal_code": addr.get("postalCode"),
        "street": addr.get("streetAddress"),
    }


def _normalize_person(rec: dict) -> dict:
    home_locs = rec.get("HomeLocation") or []
    if isinstance(home_locs, dict):
        home_locs = [home_locs]
    relatives = [
        r["name"]
        for r in (rec.get("relatedTo") or [])
        if isinstance(r, dict) and r.get("name")
    ]
    return {
        "name": rec.get("name"),
        "url": rec.get("url") or rec.get("@id"),
        "addresses": [_flatten_address(p) for p in home_locs if isinstance(p, dict)],
        "relatives": relatives,
        "source": "truepeoplesearch",
    }


def parse_listing_people(html: str) -> list[dict]:
    """Extract every Person record from a TruePeopleSearch listing page."""
    people: list[dict] = []
    seen: set[str] = set()
    for obj in _iter_json_ld(html):
        for person in _walk_persons(obj):
            pid = person.get("@id") or person.get("url") or person.get("name")
            if pid in seen:
                continue
            if pid:
                seen.add(pid)
            people.append(_normalize_person(person))
    return people


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _build_search_url(first: str, last: str, city: Optional[str], state: Optional[str]) -> str:
    first_s = (first or "").strip().lower().replace(" ", "-")
    last_s = (last or "").strip().lower().replace(" ", "-")
    name_part = f"{first_s}-{last_s}".strip("-")
    loc_bits = []
    if city:
        loc_bits.append(city.strip().lower().replace(" ", "-"))
    if state:
        loc_bits.append(state.strip().lower().replace(" ", "-"))
    suffix = "_" + "-".join(loc_bits) if loc_bits else ""
    return f"{TPS_BASE}/results?name={name_part.replace('-', '%20')}&citystatezip={'%20'.join(loc_bits)}"


async def search_people(
    first: str,
    last: str,
    city: Optional[str] = None,
    state: Optional[str] = None,
) -> dict:
    """
    Search TruePeopleSearch by name.

    Returns::

        {
          "url": str,
          "people": list[dict],   # normalized Person records
          "wall": None | dict,    # present when a challenge couldn't be solved
          "source": "truepeoplesearch",
        }

    Wall descriptor kinds:
      "datadome_proxy_missing"   — DATADOME_SOLVE_PROXY not configured
      "datadome_solve_limit"     — per-run cap exhausted
      "datadome_challenge"       — challenge remained after solve (IP mismatch likely)
      "captcha_not_configured"   — TWOCAPTCHA_API_KEY not set
      "captcha_error"            — 2Captcha returned an error
    """
    first_s = (first or "").strip().lower().replace(" ", "%20")
    last_s = (last or "").strip().lower().replace(" ", "%20")
    loc_bits = []
    if city:
        loc_bits.append(city.strip())
    if state:
        loc_bits.append(state.strip())
    loc = "%2C%20".join(loc_bits)
    if loc:
        url = f"{TPS_BASE}/results?name={first_s}%20{last_s}&citystatezip={loc.replace(' ', '%20')}"
    else:
        url = f"{TPS_BASE}/results?name={first_s}%20{last_s}"

    wall: Optional[dict] = None
    html = ""
    try:
        html, _ = await _fetch_with_datadome_solve(url)
    except DataDomeProxyNotConfigured as e:
        wall = {"kind": "datadome_proxy_missing", "detail": str(e)}
    except DataDomeSolveLimitReached as e:
        wall = {"kind": "datadome_solve_limit", "detail": str(e)}
    except DataDomeChallengePresent as e:
        wall = {"kind": "datadome_challenge", "detail": str(e)}
    except CaptchaSolverNotConfigured as e:
        wall = {"kind": "captcha_not_configured", "detail": str(e)}
    except CaptchaSolverError as e:
        wall = {"kind": "captcha_error", "detail": str(e)}

    people = parse_listing_people(html) if html and wall is None else []
    return {"url": url, "people": people, "wall": wall, "source": "truepeoplesearch"}


async def get_person_detail(detail_url: str) -> dict:
    """
    Fetch a TruePeopleSearch detail page (/find/person/<id>).

    Returns::

        {
          "url": str,
          "person": dict | None,
          "wall": None | dict,
          "source": "truepeoplesearch",
        }
    """
    wall: Optional[dict] = None
    html = ""
    try:
        html, _ = await _fetch_with_datadome_solve(detail_url)
    except DataDomeProxyNotConfigured as e:
        wall = {"kind": "datadome_proxy_missing", "detail": str(e)}
    except DataDomeSolveLimitReached as e:
        wall = {"kind": "datadome_solve_limit", "detail": str(e)}
    except DataDomeChallengePresent as e:
        wall = {"kind": "datadome_challenge", "detail": str(e)}
    except CaptchaSolverNotConfigured as e:
        wall = {"kind": "captcha_not_configured", "detail": str(e)}
    except CaptchaSolverError as e:
        wall = {"kind": "captcha_error", "detail": str(e)}

    people = parse_listing_people(html) if html and wall is None else []
    person = people[0] if people else None
    return {"url": detail_url, "person": person, "wall": wall, "source": "truepeoplesearch"}
