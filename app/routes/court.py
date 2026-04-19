"""Court docket scraper routes — search-first discovery approach."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.scraper.court import scrape_court_docket, search_court_by_number

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
