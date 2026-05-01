"""
GDELT scraper for EVENTS entity type.
Global DB of world events — dates, locations, actors, themes.
Free API, no auth required. Rate limit: 1 req/s.
"""
import httpx
import asyncio
import re
from datetime import datetime

GDELT_BASE = "https://api.gdeltproject.org/api/v2"
TIMEOUT = 20.0


async def search_gdelt(query: str, max_events: int = 20) -> list[dict]:
    """
    Search GDELT for events matching a query.
    Returns list of {event_id, date, slug, country, lat, lon, num_sources, tone}.
    Uses the Timeline Snapshots API (slices by time).
    """
    await asyncio.sleep(1.1)  # be kind
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            # Use the Search API with a text query
            r = await client.get(
                f"{GDELT_BASE}/search/search",
                params={
                    "format": "json",
                    "query": query,
                    "mode": "artlist",
                    "maxevents": max_events,
                    "sort": "DateDesc",
                },
            )
            r.raise_for_status()
            data = r.json()
            events = []
            for art in data.get("articles", [])[:max_events]:
                events.append({
                    "url": art.get("url", ""),
                    "title": art.get("title", ""),
                    "domain": art.get("domain", ""),
                    "date": art.get("published", ""),
                    "language": art.get("language", ""),
                })
            return events
    except Exception:
        return []


async def get_gdelt_event_timeline(entity: str, mode: str = "timelinevol") -> list[dict]:
    """
    Get a timeline of mentions/volatility for an entity (person/org/location).
    mode: timelinevol | timelinetone | timelinecountry | timelinegloc
    Returns list of {date, value} snapshots.
    """
    await asyncio.sleep(1.1)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(
                f"{GDELT_BASE}/timeline/timeline",
                params={
                    "format": "json",
                    "theme": entity,
                    "mode": mode,
                    "start": "20180101000000",
                    "end": "20260101000000",
                },
            )
            r.raise_for_status()
            d = r.json()
            # Parse array of {date, value} points
            return [
                {"date": p.get("date", ""), "value": p.get("value", 0)}
                for p in d.get("timeline", [])[:50]
            ]
    except Exception:
        return []


async def scrape_gdelt(query: str) -> tuple[str, list[dict]]:
    """
    Search GDELT for events matching a query and format as readable text.
    Returns (raw_text, articles_found).
    """
    if not query:
        return "[GDELT: empty query]", []

    articles = await search_gdelt(query, max_events=15)
    if not articles:
        return f"[GDELT: no results for '{query}']", []

    raw_parts = [f"[GDELT Events: {query}]"]
    entries = []
    for art in articles:
        title = art.get("title", "Untitled")
        url = art.get("url", "")
        date = art.get("date", "")
        domain = art.get("domain", "")
        if title and len(title) > 10:
            raw_parts.append(f"- {title} ({date}, {domain})")
            entries.append({"title": title, "url": url, "date": date, "domain": domain})

    if not raw_parts:
        return f"[GDELT: no readable articles for '{query}']", []

    return "\n".join(raw_parts), entries