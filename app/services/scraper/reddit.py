"""
Reddit scraper for PEOPLE entity type.
Scrapes Reddit user submitted + comments pages.
Rate-limit aware: uses REDDIT_LIMITER to prevent 429s.
Cache-aware: checks/writes entity_cache before/after scrape.
"""
import asyncio, httpx, json, os, re
from datetime import datetime
from typing import Optional, Tuple
from app.config import settings
from app.services.scraper.rate_limiter import REDDIT_LIMITER, retry_with_backoff
from app.database import SessionLocal, EntityCache

CATEGORIES = ["history", "entertainment", "geography", "science", "sports", "art_literature"]

REDDIT_SOURCE_PREFIX = "https://www.reddit.com/"


def _reddit_source_url(handle: str) -> str:
    return f"https://www.reddit.com/u/{handle.lstrip('@/')}"


def get_reddit_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing Reddit content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{REDDIT_SOURCE_PREFIX}%"),
        ).first()
        if cached:
            return cached.raw_content
    finally:
        db.close()
    return None


def save_reddit_cache(entity_name: str, entity_type: str, content: str):
    """Save scraped Reddit content to entity_cache."""
    db = SessionLocal()
    try:
        source_url = _reddit_source_url(entity_name)
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{REDDIT_SOURCE_PREFIX}%"),
        ).first()
        if existing:
            existing.raw_content = content
            existing.scraped_at = int(datetime.utcnow().timestamp())
            existing.source_url = source_url
        else:
            new_cache = EntityCache(
                entity_name=entity_name,
                entity_type=entity_type,
                raw_content=content,
                source_url=source_url,
            )
            db.add(new_cache)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


async def scrape_reddit_with_fallback(handle: str) -> str:
    """Scrape Reddit with fallback: profile -> search."""
    raw = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ObsessedBot/2.0)"}

    # Try user profile first
    async with REDDIT_LIMITER:
        for endpoint in [f"/u/{handle}/submitted.json", f"/u/{handle}/comments.json"]:
            try:
                resp = await retry_with_backoff(
                    lambda: httpx.AsyncClient(timeout=30.0, headers=headers).get(
                        f"https://www.reddit.com{endpoint}?limit=100"
                    ),
                    max_retries=3,
                    base_delay=2.0,
                )
                resp.raise_for_status()
                data = resp.json()
                posts = data.get("data", {}).get("children", [])
                for post in posts:
                    d = post["data"]
                    text = d.get("selftext") or d.get("title", "")
                    if text:
                        raw.append(f"[Reddit {d.get('subreddit','').lower()}] {text}")
            except Exception:
                pass

    # Fallback: search for user if profile scraping failed
    if not raw:
        try:
            async with REDDIT_LIMITER:
                resp = await retry_with_backoff(
                    lambda: httpx.AsyncClient(timeout=30.0, headers=headers).get(
                        f"https://www.reddit.com/search.json?q=author:{handle}&limit=100"
                    ),
                    max_retries=3,
                    base_delay=2.0,
                )
                resp.raise_for_status()
                data = resp.json()
                posts = data.get("data", {}).get("children", [])
                for post in posts:
                    d = post["data"]
                    text = d.get("selftext") or d.get("title", "")
                    if text:
                        raw.append(f"[Reddit search] {text}")
        except Exception:
            pass

    return "\n".join(raw)


async def scrape_reddit(handle: str, entity_type: str = "People") -> tuple[str, list[dict]]:
    """Scrape Reddit user submitted + comments. Returns (raw_text, posts)."""
    # Check cache first
    cached = get_reddit_cache(handle, entity_type)
    if cached:
        return cached, [{"source": "reddit", "cached": True}]

    # Scrape with fallback
    raw = await scrape_reddit_with_fallback(handle)

    # Cap content
    if len(raw) > settings.content_max_chars:
        raw = raw[:settings.content_max_chars]

    # Save to cache
    if raw:
        save_reddit_cache(handle, entity_type, raw)

    return raw, [{"source": "reddit", "cached": False}]


def clean_text(text: str) -> str:
    """Strip URLs, @mentions, and excess whitespace."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"@\w+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from scraped content via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly {50 if len(raw_content) > 500 else 25} trivia questions about them.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- correct_answer and wrong_answers must be full sentences or specific facts
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: the exact phrase from the input that inspired this question (max 20 words)
- Return ONLY the JSON array, no commentary
- If you cannot generate a question for a category, skip it"""

    user_prompt = f"Facts about {name}:\n{raw_content[: settings.content_max_chars]}"

    try:
        api_key = os.environ.get("LITELLM_API_KEY", "") or settings.litellm_api_key
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.litellm_base}/chat/completions",
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
            questions = json.loads(content)
            return questions
    except Exception as e:
        print(f"Error generating Reddit questions: {e}")
        return []