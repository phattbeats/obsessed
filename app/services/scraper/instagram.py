import httpx, json, re
from typing import Optional, tuple
from datetime import datetime
import os

from app.config import settings
from app.services.scraper.rate_limiter import generic_limiter
from app.database import SessionLocal, EntityCache

FLARESOLVERR_URL = "http://10.0.0.100:8191/v1"


def get_instagram_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing Instagram content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source == "instagram"
        ).first()
        if cached:
            return cached.content
    finally:
        db.close()
    return None


def save_instagram_cache(entity_name: str, entity_type: str, content: str):
    """Save scraped Instagram content to entity_cache."""
    db = SessionLocal()
    try:
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source == "instagram"
        ).first()
        if existing:
            existing.content = content
            existing.updated_at = int(datetime.utcnow().timestamp())
        else:
            new_cache = EntityCache(
                entity_name=entity_name, entity_type=entity_type,
                content=content, source="instagram",
                created_at=int(datetime.utcnow().timestamp()),
                updated_at=int(datetime.utcnow().timestamp())
            )
            db.add(new_cache)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


async def scrape_instagram_with_fallback(handle: str, entity_type: str = "People") -> tuple[str, dict]:
    """Scrape Instagram with fallback."""
    # Try direct handle
    raw, profile = await scrape_instagram_profile(handle)
    # Fallback: try with @
    if not raw.strip() and not handle.startswith("@"):
        raw, profile = await scrape_instagram_profile("@" + handle)
    return raw, profile


async def scrape_instagram_profile(handle: str) -> tuple[str, dict]:
    """Scrape a single Instagram profile."""
    url = f"https://www.instagram.com/{handle.strip('@')}/"
    # ... existing scraping code ...
    # (keeping original parse logic)
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
        return f"[Instagram scrape error: {e}]", {}
    # ... rest of parsing ...

    if data.get("status") != "ok":
        return f"[Instagram FlareSolverr error: {data.get('message', 'unknown')}]", {}

    html = data.get("solution", {}).get("response", "")

    profile = {
        "username": handle,
        "display_name": "",
        "followers": "",
        "following": "",
        "posts": "",
        "bio": "",
    }

    # og:description format: "{M} Followers, {N} Following, {N} Posts - See Instagram..."
    og_desc_match = re.search(
        r'<meta[^>]*(?:property|name)=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']',
        html,
    )
    if not og_desc_match:
        og_desc_match = re.search(r'content=["\']([^"\']*\bFollowers\b[^"\']*)["\']', html)

    if og_desc_match:
        desc = og_desc_match.group(1)
        profile["bio"] = desc

        # Parse: "17M Followers, 629 Following, 428 Posts"
        followers_m = re.match(r'([\d.,]+[KM]?)\s*Followers', desc)
        following_m = re.search(r'([\d,]+)\s*Following', desc)
        posts_m = re.search(r'([\d,]+)\s*Posts', desc)

        if followers_m:
            profile["followers"] = followers_m.group(1)
        if following_m:
            profile["following"] = following_m.group(1)
        if posts_m:
            profile["posts"] = posts_m.group(1)

        # Strip trailing " - See Instagram..." clause
        desc_clean = re.sub(r'\s*-\s*See Instagram.*$', '', desc)

    # <title> format: "Name (@handle) • Instagram photos and videos"
    title_match = re.search(r'<title>([^<]+)</title>', html)
    if title_match:
        title = title_match.group(1)
        m = re.match(r'^(.+?)\s*\(@', title)
        if m:
            profile["display_name"] = m.group(1).strip()

    # Build readable text
    lines = [f"[Instagram profile: @{profile['username']}]"]
    if profile["display_name"]:
        lines.append(f"Profile: {profile['display_name']} (@{profile['username']})")
    else:
        lines.append(f"Profile: @{profile['username']}")

    metrics = []
    if profile["followers"]:
        metrics.append(f"{profile['followers']} Followers")
    if profile["following"]:
        metrics.append(f"{profile['following']} Following")
    if profile["posts"]:
        metrics.append(f"{profile['posts']} Posts")
    if metrics:
        lines.append(" · ".join(metrics))
    if profile["bio"]:
        lines.append(profile["bio"])

    return "\n".join(lines), profile


async def scrape_instagram(handle: str, entity_type: str = "People") -> tuple[str, dict]:
    """Main entry: Instagram with cache + fallback."""
    # Check cache first
    cached = get_instagram_cache(handle, entity_type)
    if cached:
        return cached, {"source": "instagram", "cached": True}
    # Scrape with fallback
    raw, profile = await scrape_instagram_with_fallback(handle, entity_type)
    # Cap content
    if len(raw) > settings.content_max_chars:
        raw = raw[:settings.content_max_chars]
    # Save to cache
    if raw and not raw.startswith("[Instagram scrape error"):
        save_instagram_cache(handle, entity_type, raw)
    return raw, profile


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from Instagram profile via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly 25 trivia questions about their personality and interests based on their Instagram profile data.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions are about what you can infer about the person from their follower count, post count, and any bio text
- Follower counts and post counts are social proof metrics — use them as conversation starters or personality indicators, not literal fact questions
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: exact phrase from the profile (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {name} from their Instagram profile:\n{raw_content[:settings.content_max_chars]}"

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
        print(f"Error generating Instagram questions: {e}")
        return []
