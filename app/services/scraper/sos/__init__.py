"""
Secretary of State business-entity search — state-adapter dispatch.

Each state's API is bespoke (different platform, URL scheme, name encoding,
response shape). This package keeps the public surface stable while letting
per-state adapters live in their own modules.

Public surface (re-exported here for callers and routes):

  * `search_sos_entities(state, entity_name, ...)`
  * `search_by_owner(state, owner_name, ...)`
  * `get_entity_details(state, entity_id)`

To add a new state:

  1. Drop a new module in this package implementing the `SOSAdapter`
     Protocol from `base.py`.
  2. Append an instance to `_ADAPTERS` below.

The first adapter whose `state_keys` matches wins; unmatched states fall
through to the generic `FallbackAdapter` placeholder.
"""

from __future__ import annotations

from app.services.scraper.sos.base import (
    CanonicalRow,
    SOSAdapter,
    get_clearance,
    reset_clearance_cache,
)
from app.services.scraper.sos.fallback import FallbackAdapter, find_sos_url
from app.services.scraper.sos.ohio import (
    OHIO_API_BASE,
    OHIO_STATUS_ACTIVE,
    OHIO_STATUS_ALL,
    OHIO_STATUS_CANCELLED,
    OHIO_STATUS_DEAD,
    OHIO_STATUS_FRAUDULENT,
    OHIO_UI_ORIGIN,
    OhioAdapter,
)


# Adapter registry — first matching state_keys wins. To add a state, append
# the new adapter instance here.
_ADAPTERS: list[SOSAdapter] = [
    OhioAdapter(),
]

_FALLBACK = FallbackAdapter()


def _resolve(state: str) -> tuple[SOSAdapter | FallbackAdapter, bool]:
    """Return the adapter for `state` and a bool for "is this the fallback?"."""
    key = (state or "").strip().lower()
    for ad in _ADAPTERS:
        if key in ad.state_keys:
            return ad, False
    return _FALLBACK, True


async def search_sos_entities(
    state: str,
    entity_name: str,
    entity_type: str | None = None,  # noqa: ARG001 — kept for route compat
    use_flaresolverr: bool = False,
    status: str = OHIO_STATUS_ALL,
) -> list[CanonicalRow]:
    """Search SOS for businesses matching `entity_name` in `state`."""
    adapter, is_fallback = _resolve(state)
    if is_fallback:
        # Fallback takes the state explicitly since it isn't keyed.
        return await adapter.search_entities(  # type: ignore[call-arg]
            entity_name,
            state=state,
        )
    return await adapter.search_entities(entity_name, status=status)


async def search_by_owner(
    state: str,
    owner_name: str,
    use_flaresolverr: bool = False,
) -> list[CanonicalRow]:
    """Search for all entities where `owner_name` is the agent/registrant."""
    adapter, is_fallback = _resolve(state)
    if is_fallback:
        adapter = FallbackAdapter(use_flaresolverr=use_flaresolverr)
        return await adapter.search_by_owner(owner_name, state=state)  # type: ignore[call-arg]
    return await adapter.search_by_owner(owner_name)


async def get_entity_details(state: str, entity_id: str) -> dict:
    """Look up a specific entity by ID. Falls back to filtering a name
    search by `entity_id` — adapters with first-class detail endpoints
    can override this in a future iteration."""
    rows = await search_sos_entities(state, entity_id)
    for r in rows:
        if entity_id in str(r.get("entity_id", "")):
            return dict(r)
    return {"entity_id": entity_id, "jurisdiction": state, "status": "not found"}


__all__ = [
    # Public surface
    "search_sos_entities",
    "search_by_owner",
    "get_entity_details",
    "find_sos_url",
    # Adapter scaffolding
    "CanonicalRow",
    "SOSAdapter",
    "OhioAdapter",
    "FallbackAdapter",
    "get_clearance",
    "reset_clearance_cache",
    # Ohio constants — re-exported for callers that need to set status filters
    "OHIO_API_BASE",
    "OHIO_UI_ORIGIN",
    "OHIO_STATUS_ALL",
    "OHIO_STATUS_ACTIVE",
    "OHIO_STATUS_CANCELLED",
    "OHIO_STATUS_DEAD",
    "OHIO_STATUS_FRAUDULENT",
]
