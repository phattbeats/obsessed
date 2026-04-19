"""Court docket scraper routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.scraper.court import (
    scrape_court_docket,
    search_court_by_number,
    COURT_DOCKET_URLS,
)

router = APIRouter(prefix="/api/court", tags=["court"])


@router.get("/docket/{court_key}")
async def get_court_docket(
    court_key: str,
    search_term: str = Query(..., description="Name or case number to search"),
    case_type: str | None = None,
):
    """Search a municipal court's public docket."""
    results = await scrape_court_docket(court_key, search_term, case_type)
    return {"court": court_key, "search_term": search_term, "results": results, "count": len(results)}


@router.get("/case/{court_key}/{case_number}")
async def get_case_by_number(court_key: str, case_number: str):
    """Look up a specific case number in a court docket."""
    result = await search_court_by_number(court_key, case_number)
    if result.get("status") == "not found":
        raise HTTPException(status_code=404, detail="Case not found in docket")
    return result


@router.get("/courts")
async def list_courts():
    """List supported municipal courts."""
    return {"courts": list(COURT_DOCKET_URLS.keys())}