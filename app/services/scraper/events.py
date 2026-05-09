"""
Events scraper — aggregates multiple sources for EVENTS entity type.
Sources: Wikipedia, GDELT (global events DB), WikiNews, crawl4ai for generic URLs.
Each source feeds into the shared question-generation pipeline.
"""
from app.services.scraper.rate_limiter import EVENTS_LIMITER, retry_with_backoff
from app.services.scraper.crawl4ai import crawl4ai_scrape
from app.config import settings
import os

# ─── WikiNews ───────────────────────────────────────────────────────────────────
async def scrape_wikinvas(query: str) -> tuple[str, dict]:
    """
    Search MediaWiki-based news sources for an event.
    Uses Wikipedia API with 'news' operator for recent event coverage.
    """
    import httpx
    async with EVENTS_LIMITER:
        try:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=15.0).get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": f"{query} event 2024 OR {query} 2023 OR {query} 2025",
                        "format": "json",
                        "srlimit": 5,
                    },
                ),
                max_retries=3,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("query", {}).get("search", []):
                snippet = item.get("snippet", "")
                snippet = snippet.replace("<span class=\"searchmatch\">", "").replace("</span>", "")
                results.append({
                    "title": item.get("title", ""),
                    "snippet": snippet[:300],
                    "page_id": item.get("pageid", 0),
                })
            if not results:
                return f"[WikiNews/Wikipedia: no news results for '{query}']", {}
            text_parts = [f"[News Archive: {query}]"]
            for item in results[:5]:
                text_parts.append(f"- {item['title']}: {item['snippet']}")
            return "\n".join(text_parts), {"results": results}
        except Exception as e:
            return f"[WikiNews error: {e}]", {}


async def scrape_events(
    wikipedia_query: str = "",
    gdelt_query: str = "",
    news_url: str = "",
) -> tuple[str, list[dict]]:
    """
    Aggregate event data from all available sources.
    Pass whichever queries are relevant for the event being researched.

    Returns (raw_text, entries_found).
    raw_text: combined content from all sources
    entries_found: list of {source, label} for each successful source
    """
    from app.services.scraper.wikipedia import scrape_wikipedia
    from app.services.scraper.gdelt import scrape_gdelt

    # Check entity cache first
    from app.services.entity_cache import get_cached, write_cached
    cache_key = wikipedia_query or gdelt_query or "event"
    cached = get_cached(cache_key, "event")
    if cached:
        return cached[0], [{"source": "cache", "label": cache_key}]

    raw_parts = []
    entries = []
    failed_sources = []

    # Wikipedia (primary — description, date, location, participants)
    if wikipedia_query:
        text, meta = await scrape_wikipedia(wikipedia_query)
        if text and not text.startswith("[Wikipedia error"):
            raw_parts.append(text)
            entries.append({"source": "wikipedia", "label": meta.get("title", wikipedia_query)})
        else:
            failed_sources.append("wikipedia")

    # GDELT (global events, mentions, coverage timeline)
    # Non-fatal: if GDELT is down, Wikipedia already has the core event data
    if gdelt_query:
        try:
            text, gdelt_entries = await scrape_gdelt(gdelt_query)
            if text and not text.startswith("[GDELT: no results") and not text.startswith("[GDELT: no readable"):
                raw_parts.append(text)
                entries.append({"source": "gdelt", "label": gdelt_query, "articles": gdelt_entries})
            else:
                failed_sources.append("gdelt")
        except Exception:
            failed_sources.append("gdelt")

    # Wikipedia news search (recent coverage via Wikipedia search) — always runs when gdelt_query given
    if gdelt_query:
        text, meta = await scrape_wikinvas(gdelt_query)
        if text and not text.startswith("[WikiNews") and len(text) > 50:
            raw_parts.append(text)
            entries.append({"source": "wikinews", "label": gdelt_query})
        else:
            failed_sources.append("wikinews")

    # Generic news URL via crawl4ai
    if news_url:
        try:
            text, meta = await crawl4ai_scrape(news_url)
            if text and len(text) > 50:
                title = (meta or {}).get("title", news_url)
                raw_parts.append(f"[News Source: {title}]\n{text[:5000]}")
                entries.append({"source": "generic", "label": title})
            else:
                failed_sources.append("generic_news")
        except Exception:
            failed_sources.append("generic_news")

    if not raw_parts:
        return "[Events: no data retrieved — check inputs]", []

    if failed_sources:
        raw_parts.append(f"[Sources unavailable: {', '.join(failed_sources)}]")

    result = "\n\n---\n\n".join(raw_parts)
    if result and not result.startswith("[Events: no data"):
        write_cached(cache_key, "event", result)

    return result, entries


async def generate_event_questions(profile_id: int, raw_content: str, event_name: str) -> list[dict]:
    """
    Generate trivia questions from event data via LiteLLM.
    Uses same 6-category system as person/place/thing profiles.
    """
    import json
    import httpx
    import re

    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator specializing in historical events, sporting events, cultural moments, and notable happenings.
Given facts about {event_name}, generate exactly 50 trivia questions.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions should be about date, location, participants, outcomes, significance, and notable facts
- Include questions about WHEN it happened, WHERE, WHO was involved, WHAT happened, and WHY it matters
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories (history is most relevant here)
- source_snippet: the exact phrase from the input that inspired this question (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {event_name}:\n{raw_content[: settings.content_max_chars]}"

    try:
        api_key = settings.litellm_api_key or os.environ.get("LITELLM_API_KEY", "")
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.litellm_base}/chat/completions",
                json={
                    "model": settings.litellm_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.8,
                    "max_tokens": 4000,
                },
                headers=headers,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = re.sub(r"^```json\s*", "", content.strip())
            content = re.sub(r"\s*```$", "", content.strip())
            return json.loads(content)
    except Exception:
        return []