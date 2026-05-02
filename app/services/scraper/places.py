"""
Places scraper — aggregates multiple sources for PLACES entity type.
Primary sources: Wikipedia (description, history, facts), OpenStreetMap (geo, type),
Google Places (rating, address, reviews), travel blogs (editorial content).

Fallback chain:
  Wikipedia REST API → Wikipedia HTML scrape (crawl4ai)
  OpenStreetMap/Nominatim → GeoNames
  Travel blog URL → Wikipedia summary only (if no travel URL provided)

Cache: checks entity_cache before scraping. Cache miss scrapes and writes result.
All scrapers are rate-limit aware (no hard sleeps).
"""
from app.config import settings
from app.services.scraper.wikipedia import scrape_wikipedia
from app.services.scraper.osm import scrape_osm
from app.services.scraper.google_places import scrape_places as scrape_google_places
from app.services.scraper.travel import scrape_travel_blog


async def scrape_places(
    google_places_query: str = "",
    wikipedia_query: str = "",
    osm_query: str = "",
    travel_url: str = "",
) -> tuple[str, list[dict]]:
    """
    Aggregate place data from all available sources.
    Pass whichever queries/URLs are relevant for the place being researched.

    Returns (raw_text, places_found).
    raw_text: combined content from all sources
    places_found: list of geo objects from OSM search (most useful for geo)
    """
    # Check entity cache first
    from app.services.entity_cache import get_cached, write_cached
    cache_key = wikipedia_query or osm_query or google_places_query or "place"
    cached = get_cached(cache_key, "place")
    if cached:
        return cached[0], []

    raw_parts = []
    osm_places = []
    failed_sources = []

    # Wikipedia (primary source for description, history, facts)
    # Falls back to HTML scrape automatically inside scrape_wikipedia()
    if wikipedia_query:
        try:
            text, _ = await scrape_wikipedia(wikipedia_query)
            if text and not text.startswith("[Wikipedia error"):
                raw_parts.append(text)
            else:
                failed_sources.append("wikipedia")
        except Exception:
            failed_sources.append("wikipedia")

    # OpenStreetMap (coordinates, place type, population)
    # Falls back to GeoNames automatically inside scrape_osm()
    if osm_query:
        try:
            text, places = await scrape_osm(osm_query)
            osm_places = places
            if text and not text.startswith("[OpenStreetMap: no results"):
                raw_parts.append(text)
            else:
                failed_sources.append("osm")
        except Exception:
            failed_sources.append("osm")

    # Google Places (rating, reviews, address, types)
    if google_places_query:
        try:
            text, _ = await scrape_google_places(google_places_query)
            if text and not text.startswith("[Google Places"):
                raw_parts.append(text)
            else:
                failed_sources.append("google_places")
        except Exception:
            failed_sources.append("google_places")

    # Travel blog / TripAdvisor URL — graceful degradation to Wikipedia if unavailable
    if travel_url:
        try:
            text, _ = await scrape_travel_blog(travel_url)
            if text and len(text) > 50:
                raw_parts.append(text)
            else:
                # Fallback: try Wikipedia summary
                fallback = await _travel_fallback(wikipedia_query)
                if fallback:
                    raw_parts.append(fallback)
                failed_sources.append("travel_blog")
        except Exception:
            fallback = await _travel_fallback(wikipedia_query)
            if fallback:
                raw_parts.append(fallback)
            failed_sources.append("travel_blog")

    if not raw_parts:
        return "[Places: no data retrieved — check inputs]", []

    # Append which sources were down (informational, doesn't fail the scrape)
    if failed_sources:
        raw_parts.append(f"[Sources unavailable: {', '.join(failed_sources)}]")

    result = "\n\n---\n\n".join(raw_parts)
    if result and not result.startswith("[Places: no data"):
        write_cached(cache_key, "place", result)

    return result, osm_places


async def _travel_fallback(wikipedia_query: str) -> str:
    """When travel URL fails, try to get at least Wikipedia summary as fallback."""
    if not wikipedia_query:
        return ""
    try:
        text, _ = await scrape_wikipedia(wikipedia_query)
        if text and not text.startswith("[Wikipedia error"):
            return text
    except Exception:
        pass
    return ""


async def generate_place_questions(profile_id: int, raw_content: str, place_name: str) -> list[dict]:
    """Generate trivia questions from place data via LiteLLM."""
    import json, httpx, re

    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator specializing in places, landmarks, and travel facts.
Given facts about {place_name}, generate exactly 50 trivia questions.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions should be about notable facts, history, geography, or cultural significance
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: the exact phrase from the input (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {place_name}:\n{raw_content[: settings.content_max_chars]}"

    try:
        api_key = settings.litellm_api_key or __import__("os").environ.get("LITELLM_API_KEY", "")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "http://10.0.0.100:4000/chat/completions",
                json={
                    "model": "claude-3-5-sonnet-20241022",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.8,
                    "max_tokens": 4000,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = re.sub(r"^```json\s*", "", content.strip())
            content = re.sub(r"\s*```$", "", content.strip())
            return json.loads(content)
    except Exception:
        return []
