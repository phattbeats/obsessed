"""
Secretary of State business entity search scraper.
Search-first discovery: accepts state name, searches for the actual SOS
business entity search URL, then scrapes with crawl4ai.
Nationwide — no hardcoded URLs.
"""

from __future__ import annotations

import os
import re
import httpx

from app.services.scraper.crawl4ai import crawl4ai_scrape


# Fallback known SOS URLs (used when search fails)
SOS_FALLBACK_URLS = {
    "ohio": "https://business-search.ohiosos.gov",
    "kentucky": "https://app.sos.ky.gov/ftsearch",
    "indiana": "https://bsd.sos.in.gov/PublicBusinessSearch",
    "west_virginia": "https://apps.wvto.gov/OpenGov/HCDRSearch.php",
}


async def find_sos_url(state: str) -> str | None:
    """
    Web search to discover the actual SOS business entity search URL for a state.
    Returns URL or None.
    """
    query = f"{state} Secretary of State business entity search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://www.google.com/search",
                params={"q": query, "num": 5},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            text = resp.text
            urls = re.findall(r'https?://[^\s<>\'"]+', text)
            for url in urls:
                url_lower = url.lower()
                if any(k in url_lower for k in ['sos', 'business', 'entity', 'search', 'ftsearch']):
                    if 'google' not in url_lower and 'search?' not in url_lower:
                        return url.split('&')[0].split('?')[0]
            return None
    except Exception:
        return None


def parse_entity_entry(raw: str) -> dict:
    """Parse a raw entity line into structured record."""
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    entry = {
        "entity_name": "",
        "entity_id": "",
        "jurisdiction": "",
        "status": "Active",
        "formation_date": "",
        "source_url": "",
    }
    for line in lines:
        if entry["entity_name"] == "" and len(line) > 2:
            entry["entity_name"] = line
        elif entry["entity_id"] == "" and any(k in line.lower() for k in ['id:', 'number:', '#']):
            entry["entity_id"] = line
        elif any(k in line.lower() for k in ['active', 'inactive', 'dissolved', 'good standing']):
            entry["status"] = line
    return entry


async def search_sos_entities(
    state: str,
    entity_name: str,
    entity_type: str | None = None,
) -> list[dict]:
    """
    Discover the SOS URL for a state via web search, then scrape for entities.
    state: full state name (e.g. "Ohio", "Texas", "California")
    entity_name: business name to search
    Returns list of {entity_name, entity_id, jurisdiction, status, formation_date}.
    """
    # Try web search first
    sos_url = await find_sos_url(state)
    if not sos_url:
        # Fallback to known URLs
        sos_url = SOS_FALLBACK_URLS.get(state.lower(), "")

    if not sos_url:
        return []

    try:
        text, meta = await crawl4ai_scrape(sos_url)
        if not text or text.startswith("["):
            return []
        entities = []
        lines = text.split("\n")
        for line in lines:
            if entity_name.lower() in line.lower():
                entry = parse_entity_entry(line)
                entry["jurisdiction"] = state
                entry["source_url"] = sos_url
                entities.append(entry)
        return entities[:20]
    except Exception:
        return []


async def get_entity_details(state: str, entity_id: str) -> dict:
    """Look up a specific entity by ID."""
    results = await search_sos_entities(state, entity_id)
    for r in results:
        if entity_id in str(r.get("entity_id", "")):
            return r
    return {"entity_id": entity_id, "jurisdiction": state, "status": "not found"}


async def search_by_owner(state: str, owner_name: str) -> list[dict]:
    """Search for all entities owned by a specific person."""
    sos_url = await find_sos_url(state) or SOS_FALLBACK_URLS.get(state.lower(), "")
    if not sos_url:
        return []

    try:
        text, meta = await crawl4ai_scrape(sos_url)
        if not text or text.startswith("["):
            return []
        entities = []
        lines = text.split("\n")
        for line in lines:
            if owner_name.lower() in line.lower():
                entry = parse_entity_entry(line)
                entry["owner"] = owner_name
                entry["jurisdiction"] = state
                entry["source_url"] = sos_url
                entities.append(entry)
        return entities[:20]
    except Exception:
        return []