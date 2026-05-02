"""
GDELT scraper for EVENTS entity type.
Global DB of world events — dates, locations, actors, themes.
Free API, no auth required. Rate limit: 1 req/s.

Rate-limit aware: uses GDELT_LIMITER instead of hard sleeps.
"""
import httpx
import re
from datetime import datetime
from app.services.scraper.rate_limiter import GDELT_LIMITER, retry_with_backoff

GDELT_BASE = "https://api.gdeltproject.org/api/v2"
TIMEOUT = 20.0


async def search_gdelt(query: str, max_events: int = 20) -> list[dict]:
    """
    Search GDELT for events matching a query.
    Returns list of {event_id, date, slug, country, lat, lon, num_sources, tone}.
    """
    async with GDELT_LIMITER:
        try:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=TIMEOUT).get(
                    f"{GDELT_BASE}/search/search",
                    params={
                        "format": "json",
                        "query": query,
                        "mode": "artlist",
                        "maxevents": max_events,
                        "sort": "DateDesc",
                    },
                ),
                max_retries=3,
            )
            resp.raise_for_status()
            data = resp.json()
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
    async with GDELT_LIMITER:
        try:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=TIMEOUT).get(
                    f"{GDELT_BASE}/timeline/timeline",
                    params={
                        "format": "json",
                        "theme": entity,
                        "mode": mode,
                        "start": "20180101000000",
                        "end": "20260101000000",
                    },
                ),
                max_retries=3,
            )
            resp.raise_for_status()
            d = resp.json()
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