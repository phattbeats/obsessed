import httpx
import re

PLACES_API_KEY = "AIzaSyAVzRbhLqvqWpL0DlRW31umWaPbuJKTu5U"
PLACES_BASE = "https://maps.googleapis.com/maps/api/place"


async def search_places(query: str) -> list[dict]:
    """Search Google Places for a business by text query. Returns list of {place_id, name, rating, review_count}."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{PLACES_BASE}/textsearch/json",
                params={"query": query, "key": PLACES_API_KEY},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "OK":
                return []
            results = []
            for r in data.get("results", [])[:3]:
                results.append({
                    "place_id": r.get("place_id", ""),
                    "name": r.get("name", ""),
                    "address": r.get("formatted_address", ""),
                    "rating": r.get("rating"),
                    "review_count": r.get("user_ratings_total"),
                    "types": r.get("types", []),
                })
            return results
    except Exception:
        return []


async def scrape_places(business_names: str) -> tuple[str, list[dict]]:
    """
    Scrape Google Places for comma-separated business names.
    Returns (raw_text, businesses_found).
    Each business contributes: name, rating, review_count, address.
    """
    if not business_names:
        return "", []
    names = [n.strip() for n in business_names.split(",") if n.strip()]
    raw_parts = []
    businesses = []
    for name in names:
        places = await search_places(name)
        for p in places:
            businesses.append(p)
            raw_parts.append(
                f"[Google Places: {p['name']}]\n"
                f"  Address: {p['address']}\n"
                f"  Rating: {p['rating']}/5 ({p['review_count']} reviews)\n"
                f"  Types: {', '.join(p['types'])}"
            )
    return "\n\n".join(raw_parts), businesses
