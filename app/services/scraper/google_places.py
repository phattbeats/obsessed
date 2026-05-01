"""
Places scraper — aggregates multiple sources for PLACES entity type.
Primary sources: Wikipedia, OpenStreetMap, Google Places, travel blogs.
Each source feeds into the shared question-generation pipeline.
"""
import asyncio
from app.services.scraper.wikipedia import scrape_wikipedia, search_wikipedia
from app.services.scraper.osm import scrape_osm, search_osm
from app.services.scraper.places import scrape_places as scrape_google_places
from app.services.scraper.travel import (
    scrape_tripadvisor_url,
    scrape_travel_blog,
    scrape_generic_place,
)


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
    places_found: list of place objects from OSM search (most useful for geo)
    """
    raw_parts = []
    osm_places = []

    # Wikipedia (primary source for description, history, facts)
    if wikipedia_query:
        text, _ = await scrape_wikipedia(wikipedia_query)
        if text and not text.startswith("[Wikipedia error"):
            raw_parts.append(text)

    # OpenStreetMap (coordinates, place type, population)
    if osm_query:
        text, places = await scrape_osm(osm_query)
        osm_places = places
        if text and not text.startswith("[OpenStreetMap: no results"):
            raw_parts.append(text)

    # Google Places (rating, reviews, address, types)
    if google_places_query:
        text, _ = await scrape_google_places(google_places_query)
        if text and not text.startswith("[Google Places"):
            raw_parts.append(text)

    # Travel blog / TripAdvisor URL
    if travel_url:
        text, meta = await scrape_travel_blog(travel_url)
        if text and len(text) > 50:
            raw_parts.append(text)

    if not raw_parts:
        return "[Places: no data retrieved — check inputs]", []

    return "\n\n---\n\n".join(raw_parts), osm_places


async def generate_place_questions(profile_id: int, raw_content: str, place_name: str) -> list[dict]:
    """
    Generate trivia questions from place data via LiteLLM.
    Uses same category system as person profiles.
    """
    import json
    import httpx
    import re

    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator specializing in places, landmarks, and travel facts.
Given facts about {place_name}, generate exactly 50 trivia questions.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions should be about notable facts, history, geography, or cultural significance
- Include questions about location, founding, famous visitors, landmarks, and unique features
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: the exact phrase from the input that inspired this question (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {place_name}:\n{raw_content[:8000]}"

    try:
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
                headers={"Authorization": "Bearer sk-vantage"},
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = re.sub(r"^```json\s*", "", content.strip())
            content = re.sub(r"\s*```$", "", content.strip())
            return json.loads(content)
    except Exception:
        return []