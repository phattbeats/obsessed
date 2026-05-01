"""
OpenStreetMap scraper for PLACES entity type.
Provides authoritative lat/lon coordinates, place type, and structured address data.
Uses Nominatim (official OSM geocoder) — rate limited to 1 req/s.
"""
import httpx
import asyncio

OSM_NOMINATIM = "https://nominatim.openstreetmap.org"
OSM_SEARCH_URL = f"{OSM_NOMINATIM}/search"
OSM_DETAILS_URL = f"{OSM_NOMINATIM}/details"


async def search_osm(query: str, max_results: int = 5) -> list[dict]:
    """
    Search OpenStreetMap via Nominatim for a place.
    Returns list of {place_id, osm_type, display_name, lat, lon, type, class}.
    Rate limited: 1 req/s.
    """
    await asyncio.sleep(1.1)  # Nominatim requires max 1 req/s
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                OSM_SEARCH_URL,
                params={
                    "q": query,
                    "format": "json",
                    "limit": max_results,
                    "addressdetails": 1,
                },
                headers={"User-Agent": "ObsessedTriviaBot/1.0 (phatt.tech)"},
            )
            r.raise_for_status()
            data = r.json()
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
    except Exception as e:
        return []


async def get_osm_details(place_id: str) -> dict:
    """
    Fetch full details for an OSM place by place_id.
    Returns {type, population, wikipedia, landmark, building details, etc.}
    """
    await asyncio.sleep(1.1)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                OSM_DETAILS_URL,
                params={
                    "place_id": place_id,
                    "format": "json",
                    "extratags": 1,
                },
                headers={"User-Agent": "ObsessedTriviaBot/1.0 (phatt.tech)"},
            )
            r.raise_for_status()
            return r.json()
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

        # Fetch extra details for first/best result
        if p.get("place_id"):
            details = await get_osm_details(p["place_id"])
            extratags = details.get("extratags", {})
            if extratags:
                for key in ["population", "wikipedia", "landmark", "building", "amenity"]:
                    if key in extratags:
                        raw_parts.append(f"  {key.capitalize()}: {extratags[key]}")

    return "\n\n".join(raw_parts), places