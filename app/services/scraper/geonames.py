"""
GeoNames fallback for PLACES entity type.
Used when OpenStreetMap/Nominatim is unavailable or returns no results.
GeoNames provides authoritative place names, feature types, and coordinates.
"""
import httpx
from app.services.scraper.rate_limiter import RateLimiter

GEONAMES_LIMITER = RateLimiter(max_concurrent=1, min_interval=1.0)  # 1 req/s
GEONAMES_SEARCH = "https://api.geonames.org/searchJSON"
GEONAMES_DETAILS = "https://api.geonames.org/getJSON"


async def search_geonames(query: str, max_results: int = 5) -> list[dict]:
    """
    Search GeoNames for a place.
    Returns list of {name, country, fcl (feature class), fcode (feature code), lat, lng, population}.
    """
    async with GEONAMES_LIMITER:
        try:
            # Note: GeoNames requires a username param for non-paying users.
            # Use 'demo' for low-volume testing; in production, set GEONAMES_USERNAME env var.
            import os
            username = os.environ.get("GEONAMES_USERNAME", "demo")
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    GEONAMES_SEARCH,
                    params={
                        "q": query,
                        "maxRows": max_results,
                        "featureClass": "P",  # populated places
                        "style": "FULL",
                        "username": username,
                    },
                )
                r.raise_for_status()
                data = r.json()
                results = []
                for item in data.get("geonames", [])[:max_results]:
                    results.append({
                        "name": item.get("name", ""),
                        "country": item.get("countryName", ""),
                        "fcl": item.get("fcl", ""),       # feature class: P=populated place
                        "fcode": item.get("fcode", ""),   # feature code
                        "lat": item.get("lat", ""),
                        "lng": item.get("lng", ""),
                        "population": item.get("population", 0),
                        "alternate_names": item.get("alternateNames", []),
                    })
                return results
        except Exception:
            return []


async def get_geonames_details(geoname_id: str) -> dict:
    """
    Fetch full details for a GeoNames place by geonameId.
    Returns dict with population, timezone, wikipedia URL, etc.
    """
    async with GEONAMES_LIMITER:
        try:
            import os
            username = os.environ.get("GEONAMES_USERNAME", "demo")
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    GEONAMES_DETAILS,
                    params={
                        "geonameId": geoname_id,
                        "username": username,
                    },
                )
                r.raise_for_status()
                return r.json()
        except Exception:
            return {}


async def scrape_geonames(place_name: str) -> tuple[str, list[dict]]:
    """
    Search GeoNames for a place and format results as readable text.
    Returns (raw_text, places_found).
    """
    if not place_name:
        return "[GeoNames: empty query]", []

    places = await search_geonames(place_name, max_results=5)
    if not places:
        return f"[GeoNames: no results for '{place_name}']", []

    raw_parts = []
    for p in places:
        pop = p.get("population", 0)
        pop_str = f"{pop:,}" if pop else "unknown"
        raw_parts.append(
            f"[GeoNames: {p['name']}, {p['country']}]\n"
            f"  Feature: {p.get('fcode', 'unknown')} ({p.get('fcl', '')})\n"
            f"  Coordinates: {p.get('lat', '')}, {p.get('lng', '')}\n"
            f"  Population: {pop_str}"
        )

    return "\n\n".join(raw_parts), places