"""
OpenStreetMap scraper for PLACES entity type.
Provides authoritative lat/lon coordinates, place type, and structured address data.
Uses Nominatim (official OSM geocoder) — rate limited to 1 req/s.

Rate-limit aware: uses NOMINATIM_LIMITER to enforce 1 req/s without hard sleeps.
"""
import httpx
from app.services.scraper.rate_limiter import (
    NOMINATIM_LIMITER,
    retry_with_backoff,
)

OSM_NOMINATIM = "https://nominatim.openstreetmap.org"
OSM_SEARCH_URL = f"{OSM_NOMINATIM}/search"
OSM_DETAILS_URL = f"{OSM_NOMINATIM}/details"


async def search_osm(query: str, max_results: int = 5) -> list[dict]:
    """
    Search OpenStreetMap via Nominatim for a place.
    Returns list of {place_id, osm_type, display_name, lat, lon, type, class}.
    Rate limited: 1 req/s via NOMINATIM_LIMITER (no hard sleep).
    """
    async with NOMINATIM_LIMITER:
        try:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=15.0).get(
                    OSM_SEARCH_URL,
                    params={
                        "q": query,
                        "format": "json",
                        "limit": max_results,
                        "addressdetails": 1,
                    },
                    headers={"User-Agent": "ObsessedTriviaBot/1.0 (phatt.tech)"},
                ),
                max_retries=3,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data[:max_results]:
                results.append({
                    "place_id": item.get("place_id", ""),
                    "osm_type": item.get("type", ""),
                    "osm_class": item.get("class", ""),
                    "display_name": item.get("display_name", ""),
                    "lat": item.get("lat", ""),
                    "lon": item.get("lon", ""),
                    "type_details": item.get("type", ""),
                })
            return results
        except Exception:
            return []


async def get_osm_details(place_id: str) -> dict:
    """
    Fetch full details for an OSM place by place_id.
    Returns {type, population, wikipedia, landmark, building details, etc.}
    """
    async with NOMINATIM_LIMITER:
        try:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=15.0).get(
                    OSM_DETAILS_URL,
                    params={
                        "place_id": place_id,
                        "format": "json",
                        "extratags": 1,
                    },
                    headers={"User-Agent": "ObsessedTriviaBot/1.0 (phatt.tech)"},
                ),
                max_retries=3,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {}


async def scrape_osm(place_name: str) -> tuple[str, list[dict]]:
    """
    Full OSM scrape for a place name.
    Returns (raw_text, places_found).
    """
    places = await search_osm(place_name, max_results=5)
    if not places:
        return f"[OpenStreetMap: no results for '{place_name}']", []

    raw_parts = []
    for p in places:
        raw_parts.append(
            f"[OpenStreetMap: {p['display_name']}]\n"
            f"  Type: {p.get('osm_type','unknown')} / {p.get('osm_class','unknown')}\n"
            f"  Coordinates: {p.get('lat')}, {p.get('lon')}"
        )

        if p.get("place_id"):
            details = await get_osm_details(p["place_id"])
            extratags = details.get("extratags", {})
            if extratags:
                for key in ["population", "wikipedia", "landmark", "building", "amenity"]:
                    if key in extratags:
                        raw_parts.append(f"  {key.capitalize()}: {extratags[key]}")

    return "\n\n".join(raw_parts), places