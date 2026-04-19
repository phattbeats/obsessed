"""
Municipal court records / public docket scraper.
Searches public court websites for docket entries by name or case number.
Returns structured docket entries (case number, date, description, parties).
"""

from __future__ import annotations

import os
from typing import Optional
import httpx

from app.services.scraper.crawl4ai import crawl4ai_scrape

# Map of known municipal court public docket URLs (add as needed)
# Format: city -> docket search URL
COURT_DOCKET_URLS = {
    "columbus": "https://www.municipalcourt.org/portals/mco/Case/Search",
    "cleveland": "https://court.clevelandohio.gov/public docket/",
    "cincinnati": "https://www.cincinnati-oh.gov/court/public docket-search/",
    "toledo": "https://www.toledo.oh.gov/services/municipal-court/",
    "akron": "https://www.akroncourts.com/public-docket/",
}


def parse_docket_entry(raw: str) -> dict:
    """Parse a raw docket line into structured entry fields."""
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    entry = {"case_number": "", "date": "", "description": "", "parties": ""}
    for line in lines:
        if "case" in line.lower() and entry["case_number"] == "":
            entry["case_number"] = line
        elif entry["date"] == "" and any(month in line for month in [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]):
            entry["date"] = line
        elif entry["description"] == "" and len(line) > 20:
            entry["description"] = line
        elif entry["parties"] == "" and len(line) <= 100:
            entry["parties"] = line
    return entry


async def scrape_court_docket(
    court_key: str,
    search_term: str,
    case_type: Optional[str] = None,
) -> list[dict]:
    """
    Scrape a municipal court's public docket.
    court_key: one of the keys in COURT_DOCKET_URLS
    search_term: name or case number to search
    Returns list of {case_number, date, description, parties, url} dicts.
    """
    base_url = COURT_DOCKET_URLS.get(court_key.lower(), "")
    if not base_url:
        return []

    try:
        text, meta = await crawl4ai_scrape(base_url)
        if not text or text.startswith("["):
            return []
        entries = []
        # Split by likely case dividers
        blocks = text.split("---")
        for block in blocks[:20]:  # cap at 20 results
            if search_term.lower() in block.lower():
                entry = parse_docket_entry(block)
                entry["url"] = base_url
                entries.append(entry)
        return entries
    except Exception:
        return []


async def search_court_by_number(court_key: str, case_number: str) -> dict:
    """Look up a specific case number in a court's docket."""
    results = await scrape_court_docket(court_key, case_number)
    for r in results:
        if case_number in r.get("case_number", ""):
            return r
    return {"case_number": case_number, "status": "not found", "court": court_key}