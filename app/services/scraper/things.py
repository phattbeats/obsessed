"""
Things scraper — aggregates multiple sources for THINGS/entity type.
Sources: Wikipedia, Wikidata, OpenLibrary, crawl4ai for generic URLs.
Each source feeds into the shared question-generation pipeline.
"""
import asyncio
from app.services.scraper.wikipedia import scrape_wikipedia, search_wikipedia
from app.services.scraper.wikidata import scrape_wikidata, search_wikidata, scrape_wikidata_by_query
from app.services.scraper.openlibrary import scrape_openlibrary_by_query


async def scrape_things(
    wikipedia_query: str = "",
    wikidata_query: str = "",
    openlibrary_query: str = "",
    generic_url: str = "",
) -> tuple[str, list[dict]]:
    """
    Aggregate thing data from all available sources.
    Pass whichever queries are relevant for the thing being researched.

    Returns (raw_text, entries_found).
    raw_text: combined content from all sources
    entries_found: list of {source, entity_id, label} for each successful source
    """
    raw_parts = []
    entries = []

    # Wikipedia (primary source for description, history, notable attributes)
    if wikipedia_query:
        text, meta = await scrape_wikipedia(wikipedia_query)
        if text and not text.startswith("[Wikipedia error"):
            raw_parts.append(text)
            entries.append({"source": "wikipedia", "label": meta.get("title", wikipedia_query), "meta": meta})

    # Wikidata (structured properties, entity relationships)
    if wikidata_query:
        text, wikidata_entries = await scrape_wikidata_by_query(wikidata_query)
        if text and not text.startswith("[Wikidata: no results") and not text.startswith("[Wikidata: scrape failed"):
            raw_parts.append(text)
            entries.extend(wikidata_entries)

    # OpenLibrary (for books, works, creative things)
    if openlibrary_query:
        text, ol_entries = await scrape_openlibrary_by_query(openlibrary_query)
        if text and not text.startswith("[OpenLibrary: no results") and not text.startswith("[OpenLibrary: scrape failed"):
            raw_parts.append(text)
            entries.extend(ol_entries)

    # Generic URL via crawl4ai
    if generic_url:
        from app.services.scraper.crawl4ai import crawl4ai_scrape
        text, meta = await crawl4ai_scrape(generic_url)
        if text and len(text) > 50:
            raw_parts.append(f"[Source: {generic_url}]\n{text[:5000]}")
            entries.append({"source": "generic", "label": meta.get("title", generic_url), "meta": meta})

    if not raw_parts:
        return "[Things: no data retrieved — check inputs]", []

    return "\n\n---\n\n".join(raw_parts), entries


async def generate_thing_questions(profile_id: int, raw_content: str, thing_name: str) -> list[dict]:
    """
    Generate trivia questions from thing data via LiteLLM.
    Uses same 6-category system as person/place profiles.
    """
    import json
    import httpx
    import re

    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator specializing in objects, products, inventions, works of art, and notable things.
Given facts about {thing_name}, generate exactly 50 trivia questions.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions should be about notable attributes, history, origin, creator/designer, impact, and interesting facts
- Include questions about what the thing is, when/where it was created, who made it, and why it's notable
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: the exact phrase from the input that inspired this question (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {thing_name}:\n{raw_content[:8000]}"

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