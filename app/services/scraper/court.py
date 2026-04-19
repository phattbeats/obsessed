"""
Municipal court records / public docket scraper.
Uses search-first discovery: accepts county/city name, searches for the actual
court docket URL, then scrapes with crawl4ai. No hardcoded URLs.
"""

from __future__ import annotations

import os
import httpx

from app.services.scraper.crawl4ai import crawl4ai_scrape

# Ohio municipal court docket search query template
COURT_SEARCH_URL = "https://www.google.com/search"


async def find_court_docket_url(city_or_county: str) -> str | None:
    """
    Web search to discover the actual public docket URL for a city/county.
    Returns the first relevant docket URL found, or None.
    """
    query = f"{city_or_county} Ohio municipal court public docket search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                COURT_SEARCH_URL,
                params={"q": query, "num": 5},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            text = resp.text
            # Extract URLs from search results that look like court docket pages
            import re
            # Find URLs in search results
            urls = re.findall(r'https?://[^\s<>"]+', text)
            for url in urls:
                url_lower = url.lower()
                # Filter for court-related URLs
                if any(k in url_lower for k in ['municipal', 'court', 'docket', 'case search', 'public docket']):
                    # Skip Google cache and intermediate pages
                    if 'google' not in url_lower and 'search?' not in url_lower:
                        return url.split('&')[0].split('?')[0]
            return None
    except Exception:
        return None


def parse_docket_entry(raw: str) -> dict:
    """Parse a raw docket line into structured entry fields."""
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    entry = {
        "case_number": "",
        "date": "",
        "description": "",
        "parties": "",
    }
    months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    for line in lines:
        lower = line.lower()
        if "case" in lower and not entry["case_number"]:
            entry["case_number"] = line
        elif any(month in line for month in months) and not entry["date"]:
            entry["date"] = line
        elif entry["description"] == "" and len(line) > 20:
            entry["description"] = line
        elif entry["parties"] == "" and len(line) <= 100:
            entry["parties"] = line
    return entry


async def scrape_court_docket(
    city_or_county: str,
    search_term: str,
    case_type: str | None = None,
) -> list[dict]:
    """
    Discover the actual court docket URL for a location, then scrape it.
    city_or_county: city or county name (e.g. "Columbus", "Franklin County")
    search_term: name or case number to search
    Returns list of {case_number, date, description, parties, url} dicts.
    """
    # Step 1: discover the actual docket URL via search
    docket_url = await find_court_docket_url(city_or_county)
    if not docket_url:
        return []

    # Step 2: scrape the discovered URL
    try:
        text, meta = await crawl4ai_scrape(docket_url)
        if not text or text.startswith("["):
            return []
        entries = []
        blocks = text.split("---")
        for block in blocks[:20]:
            if search_term.lower() in block.lower():
                entry = parse_docket_entry(block)
                entry["url"] = docket_url
                entry["court"] = city_or_county
                entries.append(entry)
        return entries
    except Exception:
        return []


async def search_court_by_number(city_or_county: str, case_number: str) -> dict:
    """Look up a specific case number in a court's docket."""
    results = await scrape_court_docket(city_or_county, case_number)
    for r in results:
        if case_number in str(r.get("case_number", "")):
            return r
    return {"case_number": case_number, "status": "not found", "court": city_or_county}
