"""County auditor property records routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.scraper.auditor import (
    search_property_records,
    get_property_details,
    get_property_by_address,
    AUDITOR_SEARCH_URLS,
)

router = APIRouter(prefix="/api/auditor", tags=["auditor"])


@router.get("/search/{county}")
async def search_auditor(
    county: str,
    term: str = Query(..., description="Owner name or address to search"),
    search_type: str = "owner",
):
    """Search a county auditor for property records."""
    results = await search_property_records(county, term, search_type)
    return {"county": county, "term": term, "results": results, "count": len(results)}


@router.get("/property/{county}/{parcel_id}")
async def get_property(county: str, parcel_id: str):
    """Look up a specific property by parcel ID."""
    result = await get_property_details(county, parcel_id)
    if result.get("status") == "not found":
        raise HTTPException(status_code=404, detail="Property not found")
    return result


@router.get("/by-address/{county}")
async def get_by_address(county: str, address: str = Query(...)):
    """Look up property by street address."""
    result = await get_property_by_address(county, address)
    if result.get("status") == "not found":
        raise HTTPException(status_code=404, detail="Property not found at address")
    return result


@router.get("/counties")
async def list_counties():
    """List supported county auditors."""
    return {"counties": list(AUDITOR_SEARCH_URLS.keys())}