import httpx, json, re
from typing import Optional, Tuple
from datetime import datetime
import os

from app.config import settings
from app.services.scraper.rate_limiter import generic_limiter
from app.database import SessionLocal, EntityCache

KITTYGRAM_BASE = "https://kittygr.am"
CRAWL4AI_URL = "http://crawl4ai:11235/crawl"
CRAWL4AI_TOKEN = "Phatt-tech-2026"
INSTAGRAM_SOURCE_PREFIX = "https://www.instagram.com/"


def _instagram_source_url(handle: str) -> str:
    return f"https://www.instagram.com/{handle.strip('@/')}/"


def get_instagram_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{INSTAGRAM_SOURCE_PREFIX}%"),
        ).first()
        if cached:
            return cached.raw_content
    finally:
        db.close()
    return None


def save_instagram_cache(entity_name: str, entity_type: str, content: str):
    db = SessionLocal()
    try:
        source_url = _instagram_source_url(entity_name)
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{INSTAGRAM_SOURCE_PREFIX}%"),
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


async def scrape_instagram_with_fallback(handle: str, entity_type: str = "People") -> tuple[str, dict]:
    raw, profile = await scrape_instagram_profile(handle)
    if not raw.strip() and not handle.startswith("@"):
        raw, profile = await scrape_instagram_profile("@" + handle)
    return raw, profile


async def scrape_instagram_profile(handle: str) -> tuple[str, dict]:
    """Fetch Instagram profile via Kittygram (no API key required)."""
    clean_handle = handle.strip("@/")
    kittygram_url = f"{KITTYGRAM_BASE}/{clean_handle}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with generic_limiter.throttle():
                resp = await client.post(
                    CRAWL4AI_URL,
                    headers={"Authorization": f"Bearer {CRAWL4AI_TOKEN}"},
                    json={"urls": [kittygram_url]},
                )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return f"[Instagram scrape error: {e}]", {}

    result = (data.get("results") or [{}])[0]
    if not result.get("success"):
        return f"[Instagram scrape error: Kittygram fetch failed for @{clean_handle}]", {}

    markdown = result.get("markdown") or ""
    if isinstance(markdown, dict):
        markdown = markdown.get("raw_markdown", "")

    profile = {
        "username": clean_handle,
        "display_name": "",
        "followers": "",
        "following": "",
        "posts": "",
        "bio": "",
    }

    # Display name: "### Name \n@handle"
    name_match = re.search(r"###\s+(.+?)\s*\n", markdown)
    if name_match:
        profile["display_name"] = name_match.group(1).strip()

    # Followers / Following / Posts: "**NUMBER**\nFollowers"
    followers_m = re.search(r"\*\*([\d,]+)\*\*\s*\nFollowers", markdown)
    following_m = re.search(r"\*\*([\d,]+)\*\*\s*\nFollowing", markdown)
    posts_m = re.search(r"\*\*([\d,]+)\*\*\s*\nPosts", markdown)

    if followers_m:
        profile["followers"] = followers_m.group(1).replace(",", "")
    if following_m:
        profile["following"] = following_m.group(1).replace(",", "")
    if posts_m:
        profile["posts"] = posts_m.group(1).replace(",", "")

    # Bio: text between handle line and the stats block
    bio_match = re.search(r"@" + re.escape(clean_handle) + r"[^\n]*\n(.+?)\n\s*\*?\s*\*\*", markdown, re.DOTALL)
    if bio_match:
        bio_text = bio_match.group(1).strip()
        bio_text = re.sub(r"!\[.*?\]\(.*?\)", "", bio_text).strip()
        if bio_text:
            profile["bio"] = bio_text

    # Parse post blocks from profile listing
    # Format: ![img](...) [ handle ](url)\nPosted at: ...\nN likes\nCaption\n[ N Comments](post_url)
    post_blocks = re.findall(
        r"!\[.*?\]\((https://kittygr\.am/mediaproxy[^)]+)\)\s*\[.*?\]\(https://kittygr\.am/[^\)]+\)\s*\n"
        r"Posted at:\s*([\d\- :]+)\s*\n"
        r"([\d,]+)\s+likes\s*\n"
        r"([\s\S]*?)\n"
        r"\[\s*([\d,]+)\s*Comments\]\((https://kittygr\.am/p/[^)]+)\)",
        markdown,
    )

    posts = []
    for img_url, posted_at, likes, caption, comment_count, post_url in post_blocks:
        caption = caption.strip()
        posts.append({
            "image_url": img_url,
            "posted_at": posted_at.strip(),
            "likes": likes.replace(",", ""),
            "caption": caption,
            "comment_count": comment_count.replace(",", ""),
            "post_url": post_url,
        })
    profile["posts_data"] = posts

    # Build readable text block
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

    if posts:
        lines.append("\nRecent posts:")
        for p in posts[:8]:
            lines.append(f"  [{p['posted_at']}] {p['likes']} likes · {p['comment_count']} comments")
            lines.append(f"  {p['caption']}")
            lines.append(f"  Image: {p['image_url']}")

    return "\n".join(lines), profile


async def _fetch_post_comments(post_url: str, client: httpx.AsyncClient) -> list[str]:
    """Fetch individual comment texts from a Kittygram post page."""
    try:
        resp = await client.post(
            CRAWL4AI_URL,
            headers={"Authorization": f"Bearer {CRAWL4AI_TOKEN}"},
            json={"urls": [post_url]},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    result = (data.get("results") or [{}])[0]
    if not result.get("success"):
        return []

    md = result.get("markdown") or ""
    if isinstance(md, dict):
        md = md.get("raw_markdown", "")

    # Comments section starts at "## Comments"
    comments_section = re.split(r"##\s*Comments", md, maxsplit=1)
    if len(comments_section) < 2:
        return []

    # Each comment: "[ username ](url)\ncomment text"
    comment_texts = re.findall(r"\]\(https://kittygr\.am/[^\)]+\)\s*\n([^\n!\[]{5,300})", comments_section[1])
    return [c.strip() for c in comment_texts if c.strip()]


async def scrape_instagram(handle: str, entity_type: str = "People") -> tuple[str, dict]:
    cached = get_instagram_cache(handle, entity_type)
    if cached:
        return cached, {"source": "instagram", "cached": True}
    raw, profile = await scrape_instagram_with_fallback(handle, entity_type)
    if len(raw) > settings.content_max_chars:
        raw = raw[:settings.content_max_chars]
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
