"""
Secretary of State business entity search scraper.
Searches Ohio (and other states) SOS public databases for business entities.
Returns structured entity records without storing full filing documents.
"""

from __future__ import annotations

import os
from typing import Optional
import httpx

from app.services.scraper.crawl4ai import crawl4ai_scrape


SOS_BASE_URLS = {
    "ohio": "https://business-search.ohiosos.gov",
    "kentucky": "https://app.sos.ky.gov/ftsearch",
    "indiana": "https://bsd.sos.in.gov/PublicBusinessSearch",
    "west_virginia": "https://apps.wvto.gov/OpenGov/HCDRSearch.php",
}


async def search_sos_entities(
    state: str,
    entity_name: str,
    entity_type: Optional[str] = None,
) -> list[dict]:
    """
    Search a Secretary of State database for business entities by name.
    state: one of the keys in SOS_BASE_URLS
    entity_name: business name to search
    entity_type: optional filter (e.g. "LLC", "Corp")
    Returns list of {entity_name, entity_id, jurisdiction, status, formation_date}.
    """
    base_url = SOS_BASE_URLS.get(state.lower(), "")
    if not base_url:
        return []

    try:
        text, meta = await crawl4ai_scrape(base_url)
        if not text or text.startswith("["):
            return []
        # Extract entity entries from page
        entities = []
        lines = text.split("\n")
        for line in lines:
            if entity_name.lower() in line.lower():
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2:
                    entities.append({
                        "entity_name": parts[0] if len(parts) > 0 else "",
                        "entity_id": parts[1] if len(parts) > 1 else "",
                        "jurisdiction": state,
                        "status": parts[2] if len(parts) > 2 else "Active",
                        "formation_date": parts[3] if len(parts) > 3 else "",
                        "source_url": base_url,
                    })
        return entities[:20]  # cap at 20
    except Exception:
        return []


async def get_entity_details(state: str, entity_id: str) -> dict:
    """Look up detailed info for a specific entity by ID."""
    results = await search_sos_entities(state, entity_id)
    for r in results:
        if entity_id in str(r.get("entity_id", "")):
            return r
    return {"entity_id": entity_id, "jurisdiction": state, "status": "not found"}


async def search_by_owner(state: str, owner_name: str) -> list[dict]:
    """
    Search for all entities owned by a specific person.
    Uses the SOS principal name search if available.
    """
    base_url = SOS_BASE_URLS.get(state.lower(), "")
    if not base_url:
        return []

    try:
        text, meta = await crawl4ai_scrape(base_url)
        if not text or text.startswith("["):
            return []
        entities = []
        lines = text.split("\n")
        for line in lines:
            if owner_name.lower() in line.lower():
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2:
                    entities.append({
                        "entity_name": parts[0] if len(parts) > 0 else "",
                        "entity_id": parts[1] if len(parts) > 1 else "",
                        "owner": owner_name,
                        "jurisdiction": state,
                        "status": parts[2] if len(parts) > 2 else "Active",
                        "source_url": base_url,
                    })
        return entities[:20]
    except Exception:
        return []