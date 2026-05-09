import httpx, json, re
from typing import Optional, Tuple
from datetime import datetime
import os

from app.config import settings
from app.services.scraper.rate_limiter import generic_limiter
from app.database import SessionLocal, EntityCache

# Public Instagram-mirror instances to try in order (primary first)
IG_MIRROR_INSTANCES = [
    "https://kittygr.am",    # primary
    "https://imginn.com",    # backup 1
    "https://picnob.com",    # backup 2
]
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
    """
    Fetch Instagram profile via public IG-mirror instances.

    Tries each mirror in IG_MIRROR_INSTANCES in order; returns on the first
    that yields a non-empty username. Logs which instance succeeded for
    debugging when one host degrades. All mirrors exhausted → graceful
    sentinel (not a 500).
    """
    clean_handle = handle.strip("@/")
    last_err = ""

    for instance_base in IG_MIRROR_INSTANCES:
        try:
            mirror_url = f"{instance_base}/{clean_handle}"
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with generic_limiter:
                    resp = await client.post(
                        CRAWL4AI_URL,
                        headers={"Authorization": f"Bearer {CRAWL4AI_TOKEN}"},
                        json={"urls": [mirror_url]},
                    )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            last_err = f"{instance_base}: {e}"
            continue

        result = (data.get("results") or [{}])[0]
        if not result.get("success"):
            last_err = f"{instance_base}: crawl4ai returned success=false"
            continue

        markdown = result.get("markdown") or ""
        if isinstance(markdown, dict):
            markdown = markdown.get("raw_markdown", "")

        profile = _parse_instagram_markdown(markdown, clean_handle, instance_base)

        # Success = at minimum a non-empty username
        if profile.get("username"):
            import logging
            logging.getLogger(__name__).info(
                "Instagram scrape succeeded via %s for @%s", instance_base, clean_handle
            )
            return _format_instagram_profile(profile), profile

        # Parsed empty — try next mirror
        last_err = f"{instance_base}: parsed to empty profile"
        continue

    # All mirrors exhausted
    return (
        f"[Instagram scrape error: all {len(IG_MIRROR_INSTANCES)} mirrors failed "
        f"for @{clean_handle}{f' (last: {last_err})' if last_err else ''}]",
        {},
    )


def _expand_suffix(s: str) -> str:
    """Expand K/M/B suffixes to integer strings (e.g. '1.2M' → '1200000')."""
    s = s.strip().upper()
    try:
        if s.endswith("B"):
            return str(int(float(s[:-1]) * 1_000_000_000))
        if s.endswith("M"):
            return str(int(float(s[:-1]) * 1_000_000))
        if s.endswith("K"):
            return str(int(float(s[:-1]) * 1_000))
    except ValueError:
        pass
    return s.replace(",", "")


def _parse_instagram_markdown(markdown: str, clean_handle: str, instance_base: str) -> dict:
    """
    Instance-agnostic Instagram profile parser.

    Each mirror may emit a different markdown shape. Regexes here are lenient
    enough to handle imginn, picnob, and kittygram at the profile level.
    Post-block URL patterns use the instance_base to avoid hardcoding a single domain.
    """
    domain = instance_base.replace("https://", "")
    profile = {
        "username": clean_handle,
        "display_name": "",
        "followers": "",
        "following": "",
        "posts": "",
        "bio": "",
        "posts_data": [],
    }

    # Display name: first heading in the doc — "### Name", "## Name", or "# Name"
    name_match = re.search(r"^#{1,3}\s+(.+)$", markdown, re.MULTILINE)
    if name_match:
        profile["display_name"] = name_match.group(1).strip()

    # Followers / Following / Posts: "**N** Followers" — handles plain ints and K/M/B suffixes
    for label, key in (("Followers", "followers"), ("Following", "following"), ("Posts", "posts")):
        m = re.search(
            r"\*\*([\d,.KMBkmb]+)\s*\*?\s*" + re.escape(label),
            markdown, re.IGNORECASE
        )
        if m:
            profile[key] = _expand_suffix(m.group(1))

    # Bio: text between the @handle line and the stats block
    bio_match = re.search(
        r"@" + re.escape(clean_handle) + r"[^\n]*\n(.+?)\n\s*(?:\*?\??\d|Posts|Followers)",
        markdown, re.DOTALL
    )
    if bio_match:
        bio_text = bio_match.group(1).strip()
        bio_text = re.sub(r"!\[.*?\]\(.*?\)", "", bio_text).strip()
        if bio_text:
            profile["bio"] = bio_text

    # Post blocks — instance-aware to avoid hardcoding kittygr.am
    # Format:
    #   ![img](https://{domain}/mediaproxy/...)
    #   [ handle ](https://{domain}/p/...)
    #   Posted at: 2024-01-15 10:30
    #   1,234 likes
    #   Caption text
    #   [ 123 Comments](https://{domain}/p/...)
    post_pattern = (
        r"!\[.*?\]\(https://" + re.escape(domain) + r"/[^)]+\)"
        r"\s*\[.*?\]\(https://" + re.escape(domain) + r"/[^)]+\)"
        r"\s*\n\s*Posted at:\s*([\d\- :]+)\s*\n"
        r"([\d,]+)\s+likes\s*\n"
        r"([\s\S]*?)\n"
        r"\[\s*[\d,]+\s*Comments\]\(https://" + re.escape(domain) + r"/p/[^)]+\)"
    )
    for img_url, posted_at, likes, caption, comment_count, post_url in re.findall(post_pattern, markdown):
        profile["posts_data"].append({
            "image_url": img_url,
            "posted_at": posted_at.strip(),
            "likes": likes.replace(",", ""),
            "caption": caption.strip(),
            "comment_count": comment_count.replace(",", ""),
            "post_url": post_url,
        })

    return profile


def _format_instagram_profile(profile: dict) -> str:
    """Build readable text block from parsed profile dict."""
    lines = [f"[Instagram profile: @{profile['username']}]"]
    if profile["display_name"]:
        lines.append(f"Profile: {profile['display_name']} (@{profile['username']})")
    else:
        lines.append(f"Profile: @{profile['username']}")

    metrics = []
    for key, label in (("followers", "Followers"), ("following", "Following"), ("posts", "Posts")):
        if profile[key]:
            metrics.append(f"{profile[key]} {label}")
    if metrics:
        lines.append(" · ".join(metrics))
    if profile["bio"]:
        lines.append(profile["bio"])

    posts = profile.get("posts_data", [])
    if posts:
        lines.append("\nRecent posts:")
        for p in posts[:8]:
            lines.append(f"  [{p['posted_at']}] {p['likes']} likes · {p['comment_count']} comments")
            lines.append(f"  {p['caption']}")

    return "\n".join(lines)


async def _fetch_post_comments(post_url: str, client: httpx.AsyncClient) -> list[str]:
    """Fetch individual comment texts from an Instagram-mirror post page."""
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

    comments_section = re.split(r"##\s*Comments", md, maxsplit=1)
    if len(comments_section) < 2:
        return []

    # Match comments on any of our known mirror domains
    domain_pattern = "|".join(re.escape(b.replace("https://", "")) for b in IG_MIRROR_INSTANCES)
    comment_texts = re.findall(
        r"\]\(https://(?:" + domain_pattern + r")/[^\)]+\)\s*\n([^\n!\[\[]{5,300})",
        comments_section[1]
    )
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
                    "model": settings.litellm_model,
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