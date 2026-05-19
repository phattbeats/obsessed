"""
TikTok scraper for PEOPLE entity type.

Primary: tikwm.com /api/user/info JSON (no API key, no login).
Secondary: tnktok.com HTML via crawl4ai.

Cache-aware via entity_cache (checks/writes before/after scrape).
"""
import httpx, re, time
from typing import Optional

from app.config import settings
from app.services.scraper.rate_limiter import generic_limiter
from app.database import SessionLocal, EntityCache

TIKTOK_SOURCES = ["tikwm_json", "tnktok_html"]
TIKWM_BASE = "https://www.tikwm.com"
TNKTK_BASE = "https://www.tnktok.com"
CRAWL4AI_URL = "http://crawl4ai:11235/crawl"
TIKTOK_SOURCE_PREFIX = "https://www.tiktok.com/@"


def get_tiktok_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{TIKTOK_SOURCE_PREFIX}%"),
        ).first()
        return cached.raw_content if cached else None
    finally:
        db.close()


def save_tiktok_cache(entity_name: str, entity_type: str, content: str):
    db = SessionLocal()
    try:
        source_url = f"{TIKTOK_SOURCE_PREFIX}{entity_name.lstrip('@/')}"
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{TIKTOK_SOURCE_PREFIX}%"),
        ).first()
        if existing:
            existing.raw_content = content
            existing.scraped_at = int(time.time())
            existing.source_url = source_url
        else:
            db.add(EntityCache(
                entity_name=entity_name,
                entity_type=entity_type,
                raw_content=content,
                source_url=source_url,
            ))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


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


def _format_tiktok_profile(profile: dict) -> str:
    """Build readable text block from parsed TikTok profile dict."""
    lines = []
    username = profile.get("username", "")
    display_name = profile.get("display_name", "")
    verified = " ✓" if profile.get("verified") else ""
    lines.append(f"[TikTok profile: @{username}{verified}]")
    if display_name and display_name != username:
        lines.append(f"Display name: {display_name}")
    if profile.get("followers"):
        lines.append(f"Followers: {profile['followers']}")
    if profile.get("following"):
        lines.append(f"Following: {profile['following']}")
    if profile.get("likes"):
        lines.append(f"Likes: {profile['likes']}")
    if profile.get("videos"):
        lines.append(f"Videos: {profile['videos']}")
    if profile.get("bio"):
        lines.append(f"Bio: {profile['bio']}")
    if profile.get("private"):
        lines.append("(private account)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Source scrapers
# ---------------------------------------------------------------------------

async def _scrape_tikwm_json(handle: str) -> tuple[str, dict]:
    """
    Primary: tikwm.com /api/user/info JSON.
    Returns ("", {}) on any failure.
    """
    clean = handle.strip("@/")
    url = f"{TIKWM_BASE}/api/user/info?unique_id={clean}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            async with generic_limiter:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return "", {}

    if data.get("code") != 0:
        return "", {}

    user = data.get("data", {}).get("user", {})
    stats = data.get("data", {}).get("stats", {})
    profile = {
        "username": user.get("uniqueId", clean),
        "display_name": user.get("nickname", ""),
        "bio": user.get("signature", ""),
        "verified": bool(user.get("verified")),
        "followers": _expand_suffix(str(stats.get("followerCount", ""))),
        "following": _expand_suffix(str(stats.get("followingCount", ""))),
        "likes": _expand_suffix(str(stats.get("heartCount", ""))),
        "videos": str(stats.get("videoCount", "")),
        "create_time": user.get("createTime", 0),
        "private": bool(user.get("privateAccount")),
    }
    return _format_tiktok_profile(profile), profile


async def _scrape_tnktok_html(handle: str) -> tuple[str, dict]:
    """
    Secondary: tnktok.com via crawl4ai.
    Returns ("", {}) on any failure.
    """
    clean = handle.strip("@/")
    url = f"{TNKTK_BASE}/@{clean}"
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            async with generic_limiter:
                resp = await client.post(
                    CRAWL4AI_URL,
                    headers={"Authorization": f"Bearer {settings.crawl4ai_token}"},
                    json={"urls": [url]},
                )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return "", {}

    result = (data.get("results") or [{}])[0]
    if not result.get("success"):
        return "", {}

    md = result.get("markdown") or ""
    if isinstance(md, dict):
        md = md.get("raw_markdown", "")

    return _parse_tnktok_markdown(md, clean)


def _parse_tnktok_markdown(markdown: str, clean_handle: str) -> tuple[str, dict]:
    """Parse TikTok profile from tnktok.com markdown shape."""
    profile = {
        "username": clean_handle,
        "display_name": "",
        "bio": "",
        "verified": False,
        "followers": "",
        "following": "",
        "likes": "",
        "videos": "",
        "private": False,
    }

    # Display name: first heading "# Name"
    name_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    if name_match:
        profile["display_name"] = name_match.group(1).strip()

    # Handle line: "## handle"
    handle_match = re.search(r"^##\s+(.+)$", markdown, re.MULTILINE)
    if handle_match:
        profile["username"] = handle_match.group(1).strip().lstrip("@")

    # Followers / Following / Likes: "**N** Followers" / "**N** Following" / "**N** Likes"
    # Also handles "161.2M Followers" with suffix expansion
    for label, key in (("Followers", "followers"), ("Following", "following"), ("Likes", "likes")):
        m = re.search(
            r"\*\*([\d,.KMBkmb]+)\*\*\s*" + re.escape(label),
            markdown, re.IGNORECASE
        )
        if m:
            profile[key] = _expand_suffix(m.group(1))

    # Bio: text after handle line, before first stats block
    # Pattern: "## handle\n## bio_text" or bio on its own line before "Followers"
    bio_section = re.split(r"##\s+", markdown, maxsplit=1)
    if len(bio_section) > 1:
        bio_raw = bio_section[1][:500]
        # Stop at the stats line
        stop_match = re.search(r"\*\*[\d,.KMB]+\*\*", bio_raw)
        if stop_match:
            bio_raw = bio_raw[:stop_match.start()]
        bio_text = re.sub(r"!\[.*?\]\(.*?\)", "", bio_raw).strip()
        bio_text = re.sub(r"\*\*", "", bio_text).strip()
        if bio_text and len(bio_text) > 1:
            profile["bio"] = bio_text

    # Private marker
    profile["verified"] = bool(re.search(r"✓|verified", markdown[:300], re.IGNORECASE))
    profile["private"] = bool(re.search(r"private|account.*private", markdown[:300], re.IGNORECASE))

    if not profile.get("username"):
        return "", {}

    return _format_tiktok_profile(profile), profile


async def scrape_tiktok_profile(handle: str) -> tuple[str, dict]:
    """
    Iterate TIKTOK_SOURCES, return first non-empty parse, fall through to sentinel.
    """
    clean = handle.strip("@/")
    last_err = ""
    for source in TIKTOK_SOURCES:
        try:
            fn = {"tikwm_json": _scrape_tikwm_json, "tnktok_html": _scrape_tnktok_html}[source]
            raw, profile = await fn(handle)
            if raw and profile.get("username"):
                import logging
                logging.getLogger(__name__).info(
                    "TikTok scrape succeeded via %s for @%s", source, clean
                )
                return raw, profile
        except Exception as e:
            last_err = f"{source}: {e}"
            continue
    return (
        f"[TikTok scrape error: all sources failed for @{clean}"
        f"{f' (last: {last_err})' if last_err else ''}]",
        {},
    )


async def scrape_tiktok(handle: str, entity_type: str = "People") -> tuple[str, dict]:
    """Cache-aware wrapper, mirrors instagram.scrape_instagram."""
    cached = get_tiktok_cache(handle, entity_type)
    if cached:
        return cached, {"source": "tiktok", "cached": True}
    raw, profile = await scrape_tiktok_profile(handle)
    if len(raw) > settings.content_max_chars:
        raw = raw[:settings.content_max_chars]
    if raw and not raw.startswith("[TikTok scrape error"):
        save_tiktok_cache(handle, entity_type, raw)
    return raw, profile


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from TikTok profile via LiteLLM."""
    import json, os, re
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly 25 trivia questions about their personality and interests based on their TikTok profile data.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- TikTok bios and captions tend to be very short and meme-coded; favor questions about implied taste/personality/audience over literal text recall
- If the bio is mostly emoji, infer tone (humor, dance, sports, etc.) instead of quoting the emoji string
- Use follower/following/video counts as personality indicators, not trivia facts
- Wrong answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: exact phrase from the profile (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {name} from their TikTok profile:\n{raw_content[:settings.content_max_chars]}"

    try:
        api_key = os.environ.get("LITELLM_API_KEY", "") or settings.litellm_api_key or ""
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
            return json.loads(content)
    except Exception as e:
        print(f"Error generating TikTok questions: {e}")
        return []