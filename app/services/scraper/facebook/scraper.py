"""
Facebook scraper for PEOPLE/BUSINESS entity type.

Uses crawl4ai to fetch https://www.facebook.com/{handle}/ and
https://www.facebook.com/{handle}/about — no API key, no login required
for public pages.

Cache-aware via entity_cache (checks/writes before/after scrape).
"""
import httpx, json, re
from typing import Optional
from datetime import datetime

from app.config import settings
from app.services.scraper.rate_limiter import generic_limiter
from app.database import SessionLocal, EntityCache

CRAWL4AI_URL = "http://crawl4ai:11235/crawl"
FACEBOOK_SOURCE_PREFIX = "https://www.facebook.com/"


def get_facebook_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing Facebook content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{FACEBOOK_SOURCE_PREFIX}%"),
        ).first()
        return cached.raw_content if cached else None
    finally:
        db.close()


def save_facebook_cache(entity_name: str, entity_type: str, content: str):
    """Save scraped Facebook content to entity_cache."""
    db = SessionLocal()
    try:
        source_url = f"{FACEBOOK_SOURCE_PREFIX}{entity_name.lstrip('@/')}"
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{FACEBOOK_SOURCE_PREFIX}%"),
        ).first()
        if existing:
            existing.raw_content = content
            existing.scraped_at = int(datetime.now(timezone.utc).timestamp())
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


def _is_login_wall(markdown: str) -> bool:
    """Detect Facebook login/form wall — no page name + login form markers."""
    login_markers = [
        "Mobile number or email",
        "Create new account",
        "log in to facebook",
        "fbpwd",
        'name="pass"',
    ]
    content_lower = markdown.lower()
    return (
        sum(1 for m in login_markers if m.lower() in content_lower) >= 2
        and not re.search(r"^(?:#{1,3}\s+|\*\*).{3,}", markdown, re.MULTILINE)
    )


def _parse_facebook_page(markdown: str) -> dict:
    """
    Parse Facebook page profile markdown (from crawl4ai).
    Handles two shapes:
    1. Main page: page name, verified, follower count, latest post
    2. About page: category, description, website, contact info
    """
    profile = {
        "name": "",
        "verified": False,
        "followers": "",
        "category": "",
        "description": "",
        "website": "",
        "contact_info": "",
        "latest_post_text": "",
        "latest_post_timestamp": "",
        "latest_post_permalink": "",
        "latest_post_reactions": "",
        "latest_post_comments_count": "",
    }

    # Page name: first heading or bold line after the URL
    name_match = re.search(r"^#{1,3}\s+(.+)$|^\*\*(.+)\*\*$", markdown, re.MULTILINE)
    if name_match:
        profile["name"] = (name_match.group(1) or name_match.group(2) or "").strip()

    # Verified badge: check for ✓ / verified marker near the name
    profile["verified"] = bool(
        re.search(r"✓|verified|Verified", markdown[:500])
    )

    # Followers: "**N** Followers" or "X Followers"
    followers_m = re.search(
        r"\*\*([\d,.KM]+)\s*\*\*?\s*Followers?"
        r"|([\d,.KM]+)\s+Followers?",
        markdown, re.IGNORECASE
    )
    if followers_m:
        raw = (followers_m.group(1) or followers_m.group(2) or "").strip()
        profile["followers"] = _expand_suffix(raw)

    # Latest post block — starts at "## Latest Post" or "## Posts"
    post_section = re.split(r"##\s*(?:Latest\s+Post|Post|Pinned)", markdown, maxsplit=1)
    if len(post_section) > 1:
        post_md = post_section[1][:2000]  # only first post
        # Timestamp: "January 15, 2024 at 3:30 PM" or similar
        ts_match = re.search(
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}.*?"
            r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}.*?",
            post_md, re.IGNORECASE
        )
        if ts_match:
            profile["latest_post_timestamp"] = ts_match.group(0).strip()
        # Post body: text before reaction counts
        body_match = re.search(
            r"^(?!\s*(?:\d+\s+like|\d+\s+comment|\[|!\[)).+",
            post_md[:800], re.MULTILINE
        )
        if body_match:
            profile["latest_post_text"] = body_match.group(0).strip()
        # Reactions
        reactions_m = re.search(r"([\d,.KM]+)\s+like", post_md, re.IGNORECASE)
        if reactions_m:
            profile["latest_post_reactions"] = _expand_suffix(reactions_m.group(1))
        # Comments
        comments_m = re.search(r"([\d,.KM]+)\s+comment", post_md, re.IGNORECASE)
        if comments_m:
            profile["latest_post_comments_count"] = _expand_suffix(comments_m.group(1))
        # Permalink
        permalink_m = re.search(r"\((https://www\.facebook\.com/[^)]+)\)", post_md)
        if permalink_m:
            profile["latest_post_permalink"] = permalink_m.group(1)

    # About section
    about_section = re.split(r"##\s*About|Category\s*", markdown, maxsplit=1)
    if len(about_section) > 1:
        about_md = about_section[1][:1500]
        # Category
        cat_m = re.search(r"(?:Category|Type)[\s:]*([^\n]+)", about_md, re.IGNORECASE)
        if cat_m:
            profile["category"] = cat_m.group(1).strip()
        # Description / bio
        desc_m = re.search(r"(?:Description|Bio|About)[\s:]*([^\n]{10,500})", about_md, re.IGNORECASE)
        if desc_m:
            profile["description"] = desc_m.group(1).strip()
        # Website
        url_m = re.search(r"https?://[^\s<>\"]+", about_md)
        if url_m:
            profile["website"] = url_m.group(0)

    return profile


def _expand_suffix(s: str) -> str:
    """Expand K/M suffixes: '1.2M' → '1200000'."""
    s = s.strip().upper()
    try:
        if s.endswith("M"):
            return str(int(float(s[:-1]) * 1_000_000))
        if s.endswith("K"):
            return str(int(float(s[:-1]) * 1_000))
    except ValueError:
        pass
    return s.replace(",", "")


def _format_facebook_profile(profile: dict) -> str:
    """Build readable text block from parsed profile dict."""
    lines = []
    name = profile.get("name") or "Facebook Page"
    verified = " ✓" if profile.get("verified") else ""
    lines.append(f"[Facebook page: {name}{verified}]")
    if profile.get("followers"):
        lines.append(f"{profile['followers']} Followers")
    if profile.get("category"):
        lines.append(f"Category: {profile['category']}")
    if profile.get("description"):
        lines.append(profile["description"])
    if profile.get("website"):
        lines.append(f"Website: {profile['website']}")
    if profile.get("latest_post_timestamp"):
        lines.append(f"\nLatest post ({profile['latest_post_timestamp']}):")
        if profile.get("latest_post_text"):
            lines.append(f"  {profile['latest_post_text']}")
        if profile.get("latest_post_reactions"):
            lines.append(f"  {profile['latest_post_reactions']} likes")
        if profile.get("latest_post_comments_count"):
            lines.append(f"  {profile['latest_post_comments_count']} comments")
        if profile.get("latest_post_permalink"):
            lines.append(f"  {profile['latest_post_permalink']}")
    return "\n".join(lines)


async def _crawl_facebook(url: str) -> tuple[str, bool]:
    """
    Call crawl4ai for a single Facebook URL.
    Returns (markdown, success). success=False on any error.
    """
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
    except Exception as e:
        return f"[crawl4ai error: {e}]", False

    result = (data.get("results") or [{}])[0]
    if not result.get("success"):
        return "[Facebook: crawl4ai returned success=false]", False

    md = result.get("markdown") or ""
    if isinstance(md, dict):
        md = md.get("raw_markdown", "")
    return md, True


async def scrape_facebook(handle: str, entity_type: str = "People") -> tuple[str, dict]:
    """
    Scrape a public Facebook page via crawl4ai.
    Returns (raw_text, profile_dict).
    Cache-aware. Graceful sentinels for missing page / login wall / crawl4ai down.
    """
    handle = handle.strip("@/")
    if not handle:
        return "[Facebook: empty handle]", {}

    # Check cache first
    cached = get_facebook_cache(handle, entity_type)
    if cached:
        return cached, {"source": "facebook", "cached": True}

    # Fetch main page
    page_url = f"https://www.facebook.com/{handle}/"
    about_url = f"https://www.facebook.com/{handle}/about"

    md_main, ok = await _crawl_facebook(page_url)
    if not ok:
        # crawl4ai failed entirely
        if md_main.startswith("[crawl4ai error"):
            return md_main, {}
        # Could be login wall or missing
        if _is_login_wall(md_main):
            return "[Facebook: login wall]", {}
        return "[Facebook: page not found]", {}

    # Login wall on main page
    if _is_login_wall(md_main):
        return "[Facebook: login wall]", {}

    # Fetch about page (optional enrichment — don't fail if it errors)
    md_about = ""
    try:
        md_about_raw, about_ok = await _crawl_facebook(about_url)
        if about_ok and md_about_raw and not _is_login_wall(md_about_raw):
            md_about = md_about_raw
    except Exception:
        pass

    # Parse both pages
    profile = _parse_facebook_page(md_main)
    if md_about:
        about_profile = _parse_facebook_page(md_about)
        # Merge — about page wins for category/description/website
        for key in ("category", "description", "website"):
            if not profile.get(key) and about_profile.get(key):
                profile[key] = about_profile[key]

    # Success = at minimum a page name
    if not profile.get("name"):
        # Page fetched but no name parsed — treat as login-wall-equivalent
        return "[Facebook: login wall — page rendered but name not found]", {}

    raw = _format_facebook_profile(profile)
    raw = raw[: settings.content_max_chars]

    if raw and not raw.startswith("[Facebook:"):
        save_facebook_cache(handle, entity_type, raw)

    return raw, profile


async def scrape_facebook_profile(handle: str) -> tuple[str, dict]:
    """Alias for compatibility with trigger_scrape pattern."""
    return await scrape_facebook(handle, "People")