"""
Ohio Secretary of State Business Search adapter (PHA-794).

Drives the real `businesssearchapi.ohiosos.gov` JSON API. The loader at
`businesssearch.ohiosos.gov` lists its endpoints in `/ajax/endPoints.json`:

    NS_   business name (partial)        -> NS_<NAME>_<STATUS>
    EN_   exact business name            -> EN_<NAME>
    PN_   prior business name            -> PN_<NAME>
    AE_   agent / registrant             -> AE_<NAME>
    OI_   organizer / incorporator       -> OI_<NAME>[_<COUNTY>]
    MA_   applicant name                 -> MA_<NAME>
    DI_   document id                    -> DI_<ID>
    VD_   business details by proc id    -> VD_<PROCESSING_ID>

All API calls require Cloudflare clearance for the UI host plus
`Origin`/`Referer` matching it — otherwise Cloudflare 503s the cross-origin
call. We get clearance via the shared `base.get_clearance()` helper.
"""

from __future__ import annotations

import re

import httpx

from app.services.scraper.flaresolverr import (
    CloudflareWallError,
    FlareSolverrError,
)
from app.services.scraper.sos.base import CanonicalRow, get_clearance


OHIO_UI_ORIGIN = "https://businesssearch.ohiosos.gov"
OHIO_API_BASE = "https://businesssearchapi.ohiosos.gov"

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

# Replacement table from the UI's script.min.js — must mirror it or the URL
# hits a different backend bucket from what the form would.
_OHIO_OWNER_SUB = {"-": "$H31", "_": "$U28", "%": "$P26", "&": "$A29"}
_OHIO_AGENT_SUB = {"-": "$H31", ".": "$P11", "%": "$P26", "&": "$A29"}
_OHIO_OWNER_KEEP = re.compile(r"[^a-zA-Z0-9-.%& ]")
_OHIO_BUSINESS_KEEP = re.compile(r"[^a-zA-Z0-9-_%& ]")

OHIO_STATUS_ALL = "X"
OHIO_STATUS_ACTIVE = "A"
OHIO_STATUS_CANCELLED = "C"
OHIO_STATUS_DEAD = "D"
OHIO_STATUS_FRAUDULENT = "F"


def _encode_business_name(name: str, status: str = OHIO_STATUS_ALL) -> str:
    """Build the URL-fragment for a partial business-name search."""
    cleaned = _OHIO_BUSINESS_KEEP.sub("", name)
    encoded = re.sub(r"[-_%&]", lambda m: _OHIO_OWNER_SUB[m.group(0)], cleaned)
    encoded = re.sub(r" +(?= )", "", encoded).strip()
    return f"{encoded.upper().replace(' ', '%20')}_{status}"


def _encode_owner_name(name: str) -> str:
    """Build the URL-fragment for an agent/registrant search (AE_)."""
    cleaned = _OHIO_OWNER_KEEP.sub("", name)
    encoded = re.sub(r"[-.%&]", lambda m: _OHIO_AGENT_SUB[m.group(0)], cleaned)
    encoded = re.sub(r" +(?= )", "", encoded).strip()
    return encoded.upper().replace(" ", "%20")


async def _api_get(url: str, *, retry_on_block: bool = True) -> list[dict]:
    """GET an Ohio SOS API URL with cached Cloudflare clearance.

    Returns the `data` list from the JSON envelope (empty on "no results"
    rather than raising). Re-clears Cloudflare and retries once if the API
    answers 403/503.
    """
    cookies, ua = await get_clearance(OHIO_UI_ORIGIN)
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
        await get_clearance(OHIO_UI_ORIGIN, force=True)
        return await _api_get(url, retry_on_block=False)

    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError:
        return []
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, list) else []


def _normalize_row(row: dict, *, source_url: str) -> CanonicalRow:
    """Map an Ohio JSON row to the canonical SOS row shape."""
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


class OhioAdapter:
    """SOSAdapter for the State of Ohio."""

    state_keys: tuple[str, ...] = ("ohio", "oh")

    async def search_entities(
        self,
        entity_name: str,
        *,
        status: str = OHIO_STATUS_ALL,
        limit: int = 100,
    ) -> list[CanonicalRow]:
        if not entity_name or not entity_name.strip():
            return []
        try:
            fragment = _encode_business_name(entity_name, status=status)
            url = f"{OHIO_ENDPOINTS['name_partial']}{fragment}"
            rows = await _api_get(url)
            return [_normalize_row(r, source_url=url) for r in rows[:limit]]
        except (CloudflareWallError, FlareSolverrError, httpx.HTTPError):
            return []

    async def search_by_owner(
        self,
        owner_name: str,
        *,
        limit: int = 100,
    ) -> list[CanonicalRow]:
        if not owner_name or not owner_name.strip():
            return []
        try:
            fragment = _encode_owner_name(owner_name)
            url = f"{OHIO_ENDPOINTS['agent']}{fragment}"
            rows = await _api_get(url)
            normalized: list[CanonicalRow] = []
            for r in rows[:limit]:
                rec = _normalize_row(r, source_url=url)
                rec["owner"] = owner_name  # type: ignore[typeddict-unknown-key]
                normalized.append(rec)
            return normalized
        except (CloudflareWallError, FlareSolverrError, httpx.HTTPError):
            return []
