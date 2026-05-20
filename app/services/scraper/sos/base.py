"""
Shared scaffolding for state-specific SOS adapters.

Each state's Business-Entity search lives behind a different platform — Ohio
uses a JSON API behind Cloudflare, others run ASP.NET WebForms, server-rendered
HTML, or paywalled SOAP-style endpoints. The pieces that DO generalize live
here:

  * `CanonicalRow`     — the row shape every adapter returns
  * `SOSAdapter`       — Protocol every per-state module implements
  * `get_clearance()`  — FlareSolverr-cached Cloudflare clearance keyed by UI
                         origin, so an Ohio call doesn't burn its clearance
                         when a future Texas call comes through

The state-specific pieces (endpoint table, name encoding, response field
names) stay inside each adapter module.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol, TypedDict, runtime_checkable

import httpx

from app.services.scraper.flaresolverr import (
    CloudflareWallError,
    FlareSolverrError,
)


class CanonicalRow(TypedDict, total=False):
    """Canonical SOS row shape every adapter returns.

    Required keys: entity_name, entity_id, jurisdiction, status,
    formation_date, source_url. Everything else is optional and may be
    populated when the underlying API exposes it.
    """

    entity_name: str
    entity_id: str
    jurisdiction: str
    status: str
    formation_date: str
    source_url: str

    entity_type: str
    agent_name: str | None
    agent_effective_date: str | None
    agent_status: str | None
    state_name: str | None
    county_name: str | None
    processing_id: str | None
    owner: str  # populated by search_by_owner


@runtime_checkable
class SOSAdapter(Protocol):
    """A state's Business-Entity search adapter."""

    #: State aliases this adapter answers to (lowercase). e.g.
    #: ``("ohio", "oh")`` — dispatcher matches against these.
    state_keys: tuple[str, ...]

    async def search_entities(
        self,
        entity_name: str,
        *,
        status: str = "X",
        limit: int = 100,
    ) -> list[CanonicalRow]:
        ...

    async def search_by_owner(
        self,
        owner_name: str,
        *,
        limit: int = 100,
    ) -> list[CanonicalRow]:
        ...


# --- Cloudflare clearance cache (shared across adapters) -----------------
#
# Keyed by UI origin so a future TX/CA adapter doesn't burn the Ohio
# clearance (and vice versa). __cf_bm has a ~30 min TTL and cf_clearance is
# typically ~2h, so 15 min is a safe backstop; adapters can also force a
# refresh by passing force=True when their API returns a 5xx/403.

_CLEARANCE_TTL_SEC = 15 * 60
_clearance_cache: dict[str, dict[str, Any]] = {}
_clearance_lock = asyncio.Lock()


async def get_clearance(ui_origin: str, *, force: bool = False) -> tuple[dict[str, str], str]:
    """Return (cookies, user_agent) for the given UI origin.

    Drives FlareSolverr to navigate to the UI origin so Cloudflare hands
    out `__cf_bm` + `cf_clearance` cookies, then returns them along with
    the UA Chromium reported. Cached per-origin for ~15 min.
    """
    async with _clearance_lock:
        entry = _clearance_cache.get(ui_origin)
        if (
            not force
            and entry is not None
            and time.time() - float(entry.get("ts", 0)) < _CLEARANCE_TTL_SEC
        ):
            return entry["cookies"], entry["ua"]

        cookies, ua = await _fetch_clearance(ui_origin)
        _clearance_cache[ui_origin] = {"cookies": cookies, "ua": ua, "ts": time.time()}
        return cookies, ua


async def _fetch_clearance(ui_origin: str) -> tuple[dict[str, str], str]:
    """Hit FlareSolverr to clear Cloudflare and harvest the resulting cookies."""
    from app.services.scraper.flaresolverr import FLARESOLVERR_URL

    body = {"cmd": "request.get", "url": f"{ui_origin}/", "maxTimeout": 90000}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{FLARESOLVERR_URL}/v1", json=body)
        data = resp.json()

    if data.get("status") != "ok":
        raise FlareSolverrError(
            f"FlareSolverr clearance failed for {ui_origin}: {data.get('message', data)}",
            0,
        )

    sol = data.get("solution", {})
    cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
    ua = sol.get("userAgent") or "Mozilla/5.0"
    if "cf_clearance" not in cookies:
        raise CloudflareWallError(
            f"Cloudflare clearance for {ui_origin} did not return cf_clearance cookie",
            0,
        )
    return cookies, ua


def reset_clearance_cache() -> None:
    """Test helper: drop every cached origin."""
    _clearance_cache.clear()
