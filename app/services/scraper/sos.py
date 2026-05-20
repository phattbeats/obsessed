"""
Secretary of State business entity search scraper.

Ohio is implemented against the real `businesssearchapi.ohiosos.gov` JSON API
(PHA-794). The Angular-ish search UI at `businesssearch.ohiosos.gov` is just a
loader for the same JSON endpoints, which are listed in
`/ajax/endPoints.json`:

    NS_   business name (partial)        -> NS_<NAME>_<STATUS>
    EN_   exact business name            -> EN_<NAME>
    PN_   prior business name            -> PN_<NAME>
    AE_   agent / registrant             -> AE_<NAME>
    OI_   organizer / incorporator       -> OI_<NAME>[_<COUNTY>]
    MA_   applicant name                 -> MA_<NAME>
    DI_   document id                    -> DI_<ID>
    VD_   business details by proc id    -> VD_<PROCESSING_ID>

All API calls require:
  1. Cloudflare clearance cookies (`__cf_bm`, `cf_clearance`) obtained by
     navigating to `https://businesssearch.ohiosos.gov/` first.
  2. `Origin` + `Referer: https://businesssearch.ohiosos.gov/` and the
     matching `User-Agent`, otherwise Cloudflare 503s the cross-origin call.

We get (1) and the UA via FlareSolverr — the Cloudflare-clearing browser
proxy already used by the rest of the scraper stack. Once we have the
cookies + UA we make the actual API GETs with plain httpx, which is faster
and doesn't need a browser context per query.

Other states keep their existing search-first placeholder behavior — they
were intentional scaffolding and don't have a confirmed endpoint to call.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import httpx

from app.services.scraper.crawl4ai import crawl4ai_scrape
from app.services.scraper.flaresolverr import (
    CloudflareWallError,
    FlareSolverrError,
    fs_get,
)


# --- Ohio API constants --------------------------------------------------

OHIO_UI_ORIGIN = "https://businesssearch.ohiosos.gov"
OHIO_API_BASE = "https://businesssearchapi.ohiosos.gov"

# Endpoint prefixes — sourced from /ajax/endPoints.json on the UI host.
OHIO_ENDPOINTS = {
    "name_partial": f"{OHIO_API_BASE}/NS_",
    "name_exact": f"{OHIO_API_BASE}/EN_",
    "name_prior": f"{OHIO_API_BASE}/PN_",
    "agent": f"{OHIO_API_BASE}/AE_",
    "organizer": f"{OHIO_API_BASE}/OI_",
    "applicant": f"{OHIO_API_BASE}/MA_",
    "document_id": f"{OHIO_API_BASE}/DI_",
    "details": f"{OHIO_API_BASE}/VD_",
}

# Replacement table from the UI script (script.min.js) — must mirror it so
# the URL we build hits the same backend bucket the form would.
_OHIO_OWNER_SUB = {"-": "$H31", "_": "$U28", "%": "$P26", "&": "$A29"}
_OHIO_OWNER_KEEP = re.compile(r"[^a-zA-Z0-9-.%& ]")
_OHIO_BUSINESS_KEEP = re.compile(r"[^a-zA-Z0-9-_%& ]")

# Status filter codes for the partial business-name search.
OHIO_STATUS_ALL = "X"
OHIO_STATUS_ACTIVE = "A"
OHIO_STATUS_CANCELLED = "C"
OHIO_STATUS_DEAD = "D"
OHIO_STATUS_FRAUDULENT = "F"


# --- Cloudflare clearance cache -----------------------------------------

# __cf_bm has a ~30 min TTL and cf_clearance is typically ~2h. Cache for ~15
# min and refresh on the next call. Refresh is also forced by `fetch_clearance`
# when the API returns a 5xx/403, so a hard TTL is only a backstop.
_CLEARANCE_TTL_SEC = 15 * 60
_clearance_cache: dict[str, object] = {}
_clearance_lock = asyncio.Lock()


async def _get_clearance(force: bool = False) -> tuple[dict[str, str], str]:
    """Return (cookies, user_agent) for cross-origin calls to the Ohio API.

    Uses an in-process cache so a burst of queries shares one Cloudflare
    clearance hit (which takes ~5-30 s through FlareSolverr).
    """
    async with _clearance_lock:
        if not force and _clearance_cache:
            if time.time() - float(_clearance_cache.get("ts", 0)) < _CLEARANCE_TTL_SEC:
                return _clearance_cache["cookies"], _clearance_cache["ua"]  # type: ignore[return-value]

        cookies, ua = await _fetch_clearance()
        _clearance_cache["cookies"] = cookies
        _clearance_cache["ua"] = ua
        _clearance_cache["ts"] = time.time()
        return cookies, ua


async def _fetch_clearance() -> tuple[dict[str, str], str]:
    """Drive FlareSolverr to clear Cloudflare on the UI host and return the
    resulting cookies + the UA Chromium reported."""
    # fs_get returns (html, status) and discards the raw FlareSolverr envelope,
    # so reach for the lower-level FlareSolverr endpoint directly for cookies.
    from app.services.scraper.flaresolverr import FLARESOLVERR_URL  # local to avoid cycle at import

    body = {
        "cmd": "request.get",
        "url": f"{OHIO_UI_ORIGIN}/",
        "maxTimeout": 90000,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{FLARESOLVERR_URL}/v1", json=body)
        data = resp.json()

    if data.get("status") != "ok":
        raise FlareSolverrError(
            f"FlareSolverr clearance failed: {data.get('message', data)}", 0
        )
    sol = data.get("solution", {})
    cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
    ua = sol.get("userAgent") or "Mozilla/5.0"
    if "cf_clearance" not in cookies:
        # No clearance cookie means Cloudflare didn't actually let us through.
        raise CloudflareWallError(
            "Cloudflare clearance did not return cf_clearance cookie", 0
        )
    return cookies, ua


# --- Ohio API client -----------------------------------------------------


def _encode_business_name(name: str, status: str = OHIO_STATUS_ALL) -> str:
    """Build the URL-fragment for a partial business-name search."""
    cleaned = _OHIO_BUSINESS_KEEP.sub("", name)
    # Owner-style chars get replaced by tokens; only owner chars apply to
    # business-name search per the UI script's `case "bSearch"` branch.
    encoded = re.sub(
        r"[-_%&]",
        lambda m: _OHIO_OWNER_SUB[m.group(0)],
        cleaned,
    )
    encoded = re.sub(r" +(?= )", "", encoded).strip()
    return f"{encoded.upper().replace(' ', '%20')}_{status}"


def _encode_owner_name(name: str) -> str:
    """Build the URL-fragment for an agent/registrant search (AE_)."""
    cleaned = _OHIO_OWNER_KEEP.sub("", name)
    encoded = re.sub(
        r"[-.%&]",
        lambda m: {"-": "$H31", ".": "$P11", "%": "$P26", "&": "$A29"}[m.group(0)],
        cleaned,
    )
    encoded = re.sub(r" +(?= )", "", encoded).strip()
    return encoded.upper().replace(" ", "%20")


async def _ohio_api_get(url: str, *, retry_on_block: bool = True) -> list[dict]:
    """GET an Ohio SOS API URL with the Cloudflare clearance cached above.

    Returns the `data` list from the JSON response (an empty list on
    'no results' rather than raising). Raises CloudflareWallError or
    httpx errors if the request fundamentally fails.
    """
    cookies, ua = await _get_clearance()
    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": OHIO_UI_ORIGIN,
        "Referer": f"{OHIO_UI_ORIGIN}/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    async with httpx.AsyncClient(timeout=30, cookies=cookies, headers=headers) as c:
        resp = await c.get(url)

    if resp.status_code in (403, 503) and retry_on_block:
        # Clearance probably expired or got rotated — force-refresh once.
        await _get_clearance(force=True)
        return await _ohio_api_get(url, retry_on_block=False)

    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError:
        return []
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, list) else []


def _normalize_ohio_row(row: dict, *, source_url: str) -> dict:
    """Normalize an Ohio JSON row into the canonical SOS record shape."""
    return {
        "entity_name": row.get("business_name", "") or "",
        "entity_id": str(row.get("charter_num", "") or ""),
        "jurisdiction": "Ohio",
        "status": row.get("status", "") or "",
        "formation_date": (row.get("effect_date") or "")[:10],
        "entity_type": row.get("business_type", "") or "",
        "agent_name": (row.get("agent_name") or "").strip() or None,
        "agent_effective_date": (row.get("agent_effective_date") or "")[:10] or None,
        "agent_status": row.get("agent_status") or None,
        "state_name": row.get("state_name") or None,
        "county_name": row.get("county_name") or None,
        "processing_id": row.get("processing_id") or None,
        "source_url": source_url,
    }


async def _ohio_search_entities(
    entity_name: str,
    *,
    status: str = OHIO_STATUS_ALL,
    limit: int = 100,
) -> list[dict]:
    fragment = _encode_business_name(entity_name, status=status)
    url = f"{OHIO_ENDPOINTS['name_partial']}{fragment}"
    rows = await _ohio_api_get(url)
    return [_normalize_ohio_row(r, source_url=url) for r in rows[:limit]]


async def _ohio_search_by_owner(owner_name: str, *, limit: int = 100) -> list[dict]:
    fragment = _encode_owner_name(owner_name)
    url = f"{OHIO_ENDPOINTS['agent']}{fragment}"
    rows = await _ohio_api_get(url)
    normalized = []
    for r in rows[:limit]:
        rec = _normalize_ohio_row(r, source_url=url)
        rec["owner"] = owner_name
        normalized.append(rec)
    return normalized


# --- Placeholder fallback (non-Ohio) -------------------------------------

SOS_FALLBACK_URLS = {
    "ohio": "https://businesssearch.ohiosos.gov",
    "kentucky": "https://app.sos.ky.gov/ftsearch",
    "indiana": "https://bsd.sos.in.gov/PublicBusinessSearch",
    "west_virginia": "https://apps.wvto.gov/OpenGov/HCDRSearch.php",
}


async def find_sos_url(state: str) -> str | None:
    """Web-search the SOS business entity search URL for a state. Returns
    None on miss. Used as a generic fallback for non-Ohio states."""
    query = f"{state} Secretary of State business entity search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://www.google.com/search",
                params={"q": query, "num": 5},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            urls = re.findall(r'https?://[^\s<>\'"]+', resp.text)
            for url in urls:
                url_lower = url.lower()
                if any(k in url_lower for k in ["sos", "business", "entity", "search", "ftsearch"]):
                    if "google" not in url_lower and "search?" not in url_lower:
                        return url.split("&")[0].split("?")[0]
            return None
    except Exception:
        return None


def _parse_entity_line(raw: str) -> dict:
    """Best-effort parse of an HTML/text line containing an entity hit."""
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    entry = {
        "entity_name": "",
        "entity_id": "",
        "jurisdiction": "",
        "status": "Active",
        "formation_date": "",
        "source_url": "",
    }
    for line in lines:
        if not entry["entity_name"] and len(line) > 2:
            entry["entity_name"] = line
        elif not entry["entity_id"] and any(k in line.lower() for k in ["id:", "number:", "#"]):
            entry["entity_id"] = line
        elif any(k in line.lower() for k in ["active", "inactive", "dissolved", "good standing"]):
            entry["status"] = line
    return entry


async def _fallback_text_search(
    state: str,
    needle: str,
    use_flaresolverr: bool,
    extra: Optional[dict] = None,
) -> list[dict]:
    """Generic placeholder pipeline kept for non-Ohio states."""
    sos_url = await find_sos_url(state) or SOS_FALLBACK_URLS.get(state.lower(), "")
    if not sos_url:
        return []

    try:
        text, _ = await crawl4ai_scrape(sos_url)
        haystack: str | None = text if text and not text.startswith("[") else None
        if haystack is None and use_flaresolverr:
            try:
                html, status = await fs_get(sos_url)
                if html and isinstance(status, int) and status < 400:
                    haystack = html
            except CloudflareWallError:
                haystack = None

        if not haystack:
            return []

        out: list[dict] = []
        for line in haystack.split("\n"):
            if needle.lower() in line.lower():
                entry = _parse_entity_line(line)
                entry["jurisdiction"] = state
                entry["source_url"] = sos_url
                if extra:
                    entry.update(extra)
                out.append(entry)
        return out[:20]
    except Exception:
        return []


# --- Public surface ------------------------------------------------------


def _is_ohio(state: str) -> bool:
    return state.strip().lower() in {"ohio", "oh"}


async def search_sos_entities(
    state: str,
    entity_name: str,
    entity_type: str | None = None,  # noqa: ARG001 — kept for route compat
    use_flaresolverr: bool = False,
    status: str = OHIO_STATUS_ALL,
) -> list[dict]:
    """Search SOS for businesses matching `entity_name` in `state`.

    Returns a list of {entity_name, entity_id, jurisdiction, status,
    formation_date, ...}. Ohio uses the real JSON API; other states use
    the legacy text-scrape fallback.
    """
    if not entity_name or not entity_name.strip():
        return []

    if _is_ohio(state):
        try:
            return await _ohio_search_entities(entity_name, status=status)
        except (CloudflareWallError, FlareSolverrError, httpx.HTTPError):
            return []

    return await _fallback_text_search(state, entity_name, use_flaresolverr)


async def get_entity_details(state: str, entity_id: str) -> dict:
    """Look up a specific entity by ID. For Ohio this re-runs the search
    and filters; full per-entity detail records live behind VD_<proc_id>
    rather than charter number, which we don't have at this point."""
    if _is_ohio(state):
        try:
            rows = await _ohio_search_entities(entity_id)
        except (CloudflareWallError, FlareSolverrError, httpx.HTTPError):
            rows = []
        for r in rows:
            if entity_id in str(r.get("entity_id", "")):
                return r

    results = await search_sos_entities(state, entity_id)
    for r in results:
        if entity_id in str(r.get("entity_id", "")):
            return r
    return {"entity_id": entity_id, "jurisdiction": state, "status": "not found"}


async def search_by_owner(
    state: str,
    owner_name: str,
    use_flaresolverr: bool = False,
) -> list[dict]:
    """Search for all entities where `owner_name` is the agent/registrant."""
    if not owner_name or not owner_name.strip():
        return []

    if _is_ohio(state):
        try:
            return await _ohio_search_by_owner(owner_name)
        except (CloudflareWallError, FlareSolverrError, httpx.HTTPError):
            return []

    return await _fallback_text_search(
        state, owner_name, use_flaresolverr, extra={"owner": owner_name}
    )
