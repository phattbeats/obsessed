"""Secretary of State business entity search routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.scraper.sos import (
    search_sos_entities,
    get_entity_details,
    search_by_owner,
    SOS_BASE_URLS,
)

router = APIRouter(prefix="/api/sos", tags=["sos"])


@router.get("/search/{state}")
async def search_entities(
    state: str,
    name: str = Query(..., description="Business entity name to search"),
    entity_type: str | None = None,
):
    """Search a Secretary of State database for business entities by name."""
    results = await search_sos_entities(state, name, entity_type)
    return {"state": state, "name": name, "results": results, "count": len(results)}


@router.get("/entity/{state}/{entity_id}")
async def get_entity(state: str, entity_id: str):
    """Look up a specific business entity by ID."""
    result = await get_entity_details(state, entity_id)
    if result.get("status") == "not found":
        raise HTTPException(status_code=404, detail="Entity not found")
    return result


@router.get("/owner/{state}")
async def search_by_owner_endpoint(
    state: str,
    owner: str = Query(..., description="Owner/officer name to search"),
):
    """Search for all entities owned by a specific person."""
    results = await search_by_owner(state, owner)
    return {"state": state, "owner": owner, "results": results, "count": len(results)}


@router.get("/states")
async def list_states():
    """List supported Secretary of State jurisdictions."""
    return {"states": list(SOS_BASE_URLS.keys())}