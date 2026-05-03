"""
Threads scraper with cache layer, rate limiting, and fallbacks.
Updated: PHA-307 (BYOK), PHA-308 (content cap), PHA-309 (fallbacks), PHA-310 (rate limiter), PHA-335 (cache)
"""
import httpx
import json
import re
from typing import Optional, Tuple
from datetime import datetime
import os

from app.config import settings
from app.services.scraper.rate_limiter import generic_limiter
from app.database import SessionLocal, EntityCache

FLARESOLVERR_URL = "http://10.0.0.100:8191/v1"
THREADS_SOURCE_PREFIX = "https://threads.net/"


def _threads_source_url(handle: str) -> str:
    return f"https://threads.net/{handle.strip('@/')}/"


def get_threads_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing Threads content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{THREADS_SOURCE_PREFIX}%"),
        ).first()
        if cached:
            return cached.raw_content
    finally:
        db.close()
    return None


def save_threads_cache(entity_name: str, entity_type: str, content: str):
    """Save scraped Threads content to entity_cache."""
    db = SessionLocal()
    try:
        source_url = _threads_source_url(entity_name)
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{THREADS_SOURCE_PREFIX}%"),
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


async def scrape_threads_with_fallback(handle: str, entity_type: str = "People") -> tuple[str, dict]:
    """Scrape Threads with fallback."""
    raw, profile = await scrape_threads_profile(handle)
    if not raw.strip() and not handle.startswith("@"):
        raw, profile = await scrape_threads_profile("@" + handle)
    return raw, profile


async def scrape_threads_profile(handle: str) -> tuple[str, dict]:
    """Scrape a single Threads profile."""
    url = f"https://threads.net/{handle.strip('@')}/"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with generic_limiter.throttle():
                resp = await client.post(
                    FLARESOLVERR_URL,
                    json={"cmd": "request.get", "url": url, "maxTimeout": 45000},
                )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"[Threads scrape error: {e}]", {}

    if data.get("status") != "ok":
        return f"[Threads FlareSolverr error: {data.get('message', 'unknown')}]", {}

    html = data.get("solution", {}).get("response", "")
    return parse_threads_html(html, handle)


def parse_threads_html(html: str, handle: str) -> tuple[str, dict]:
    """Parse Threads HTML for profile data."""
    profile = {"username": handle.strip("@"), "display_name": "", "follower_count": "", "thread_count": "", "bio": ""}

    og_match = re.search(
        r'<meta[^>]*(?:property|name)=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']',
        html,
    )
    if not og_match:
        og_match = re.search(r'content=["\']([^"\']*Followers[^"\']*)["\']', html)

    if og_match:
        desc = og_match.group(1)
        profile["bio"] = desc
        desc = re.sub(r'\. See the latest.*$', '', desc)

        followers_match = re.match(r'([\d.,]+[KM]?)\s*Followers', desc)
        threads_match = re.search(r'([\d,]+)\s*Threads', desc)

        if followers_match:
            profile["follower_count"] = followers_match.group(1)
        if threads_match:
            profile["thread_count"] = threads_match.group(1)

        parts = desc.split('•')
        if len(parts) >= 3:
            profile["bio"] = parts[-1].strip()

    title_match = re.search(r'<title>([^<]+)</title>', html)
    if title_match:
        title = title_match.group(1)
        m = re.match(r'^(.+?)\s*\(@', title)
        if m:
            profile["display_name"] = m.group(1).strip()

    lines = [f"[Threads profile: @{profile['username']}]"]
    lines.append(f"Profile: {profile['display_name'] or profile['username']} (@{profile['username']})")
    parts_info = []
    if profile["follower_count"]:
        parts_info.append(f"{profile['follower_count']} Followers")
    if profile["thread_count"]:
        parts_info.append(f"{profile['thread_count']} Threads")
    if parts_info:
        lines.append(" · ".join(parts_info) + " · " + profile["bio"])
    elif profile["bio"]:
        lines.append(profile["bio"])

    return "\n".join(lines), profile


async def scrape_threads(handle: str, entity_type: str = "People") -> tuple[str, dict]:
    """Main entry: Threads with cache + fallback."""
    cached = get_threads_cache(handle, entity_type)
    if cached:
        return cached, {"source": "threads", "cached": True}

    raw, profile = await scrape_threads_with_fallback(handle, entity_type)

    if len(raw) > settings.content_max_chars:
        raw = raw[:settings.content_max_chars]

    if raw and not raw.startswith("[Threads scrape error"):
        save_threads_cache(handle, entity_type, raw)

    return raw, profile


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from Threads profile via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly 25 trivia questions about their personality and interests based on their Threads.net profile data.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions are about what you can infer about the person from their Threads bio and social metrics
- correct_answer and wrong_answers must be specific facts from the profile
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: exact phrase from the bio or profile (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {name} from their Threads.net profile:\n{raw_content[:settings.content_max_chars]}"

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
        print(f"Error generating Threads questions: {e}")
        return []
