import asyncio
import os
import httpx

PLACES_BASE = "https://maps.googleapis.com/maps/api/place"


def _get_places_api_key() -> str:
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        # Legacy fallback — do not commit with this key present
        key = "AIzaSyAVzRbhLqvqWpL0DlRW31umWaPbuJKTu5U"
    return key


async def search_places(query: str, max_results: int = 5) -> list[dict]:
    """
    Search Google Places for a business by text query.
    Returns list of {place_id, name, address, rating, review_count, types}.
    Raises ValueError on API-level errors so callers can handle explicitly.
    """
    api_key = _get_places_api_key()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{PLACES_BASE}/textsearch/json",
            params={"query": query, "key": api_key},
        )
        resp.raise_for_status()
        data = resp.json()

    status = data.get("status", "UNKNOWN")
    if status == "REQUEST_DENIED":
        raise ValueError(f"Places API REQUEST_DENIED: {data.get('error_message', 'check API key and billing')}")
    if status == "OVER_QUERY_LIMIT":
        raise ValueError("Places API OVER_QUERY_LIMIT — quota exceeded")
    if status == "INVALID_REQUEST":
        raise ValueError(f"Places API INVALID_REQUEST: {data.get('error_message', 'malformed query')}")
    if status != "OK" and status != "ZERO_RESULTS":
        raise ValueError(f"Places API returned status: {status}")

    results = []
    for r in data.get("results", [])[:max_results]:
        results.append({
            "place_id": r.get("place_id", ""),
            "name": r.get("name", ""),
            "address": r.get("formatted_address", ""),
            "rating": r.get("rating"),
            "review_count": r.get("user_ratings_total"),
            "types": r.get("types", []),
        })
    return results


async def scrape_places(business_names: str) -> tuple[str, list[dict]]:
    """
    Scrape Google Places for comma-separated business names.
    Returns (raw_text, businesses_found).
    Each business contributes: name, rating, review_count, address, types.
    Rate-limited: 300ms delay between names to avoid quota spikes.
    """
    if not business_names:
        return "", []
    names = [n.strip() for n in business_names.split(",") if n.strip()]
    raw_parts = []
    businesses = []
    errors = []

    for name in names:
        try:
            places = await search_places(name)
            if not places:
                errors.append(f"No results for '{name}'")
            for p in places:
                businesses.append(p)
                raw_parts.append(
                    f"[Google Places: {p['name']}]\n"
                    f"  Address: {p['address']}\n"
                    f"  Rating: {p['rating']}/5 ({p['review_count']} reviews)\n"
                    f"  Types: {', '.join(p['types'])}"
                )
        except ValueError as e:
            errors.append(f"Error for '{name}': {e}")
        except Exception as e:
            errors.append(f"Unexpected error for '{name}': {e}")

        # Rate limit between calls
        if len(names) > 1:
            await asyncio.sleep(0.3)

    if errors:
        raw_parts.append(f"[Google Places errors] {'; '.join(errors)}")

    return "\n\n".join(raw_parts), businesses
