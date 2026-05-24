"""Court docket scraper routes — municipal search-first + Franklin probate adapter."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.scraper.court import scrape_court_docket, search_court_by_number
from app.services.scraper.probate import search_probate_by_name

router = APIRouter(prefix="/api/court", tags=["court"])


@router.get("/docket")
async def get_court_docket(
    location: str = Query(..., description="City or county name (e.g. 'Columbus', 'Franklin County')"),
    search_term: str = Query(..., description="Name or case number to search"),
    case_type: str | None = None,
):
    """
    Discover the actual court docket URL for a location via web search,
    then scrape matching entries. No hardcoded URLs.
    """
    results = await scrape_court_docket(location, search_term, case_type)
    return {"location": location, "search_term": search_term, "results": results, "count": len(results)}


@router.get("/case/{location}/{case_number}")
async def get_case_by_number(location: str, case_number: str):
    """Look up a specific case number in a discovered court docket."""
    result = await search_court_by_number(location, case_number)
    if result.get("status") == "not found":
        raise HTTPException(status_code=404, detail="Case not found in docket")
    return result


@router.get("/probate/search")
async def search_franklin_probate(
    last_name: str = Query(..., description="Last name to search (e.g. 'Smith')"),
    first_name: str = Query("", description="Optional first name (e.g. 'John')"),
    max_pages: int = Query(2, ge=1, le=5, description="Max result pages to fetch (1 page ≈ 40 rows)"),
):
    """
    Search Franklin County Ohio Probate Court by case name.
    Returns estate, guardianship, trust, and other probate case rows.
    No login required — public General Case Search.
    """
    rows = await search_probate_by_name(last_name, first_name, max_pages=max_pages)
    return {
        "court": "Franklin County Probate",
        "last_name": last_name,
        "first_name": first_name,
        "count": len(rows),
        "results": rows,
    }
