"""
Goodreads scraper for PEOPLE entity type.
Scrapes a public Goodreads updates RSS feed (no auth) for recent reads,
ratings, and review snippets. Highest-signal source for book taste.

NOTE: Goodreads's RSS requires the user's *numeric* Goodreads ID, not the
display name. Users can find theirs from their profile URL
(`/user/show/{numeric_id}-{name}`). We don't try to resolve name→ID
because that requires an additional page fetch and the public profile
page is JS-rendered.

Rate-limit aware: uses GOODREADS_LIMITER.
Cache-aware: checks/writes entity_cache before/after scrape.
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional, Tuple

import httpx

from app.config import settings
from app.database import EntityCache, SessionLocal
from app.services.scraper.rate_limiter import GOODREADS_LIMITER, retry_with_backoff

GOODREADS_BASE = "https://www.goodreads.com"
GOODREADS_SOURCE_PREFIX = f"{GOODREADS_BASE}/user/"

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
}


def _goodreads_source_url(user_id: str) -> str:
    return f"{GOODREADS_BASE}/user/show/{user_id}"


# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_goodreads_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing Goodreads content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{GOODREADS_SOURCE_PREFIX}%"),
        ).first()
        if cached:
            return cached.raw_content
    finally:
        db.close()
    return None


def save_goodreads_cache(entity_name: str, entity_type: str, content: str, source_url: str) -> None:
    """Save scraped Goodreads content to entity_cache."""
    db = SessionLocal()
    try:
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{GOODREADS_SOURCE_PREFIX}%"),
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


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

_IMG_TAG_RE = re.compile(r"<img[^>]*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Title forms we care about:
#   "{name} gave 5 stars to '{book title}'"
#   "{name} added '{book title}'"
#   "{name} started reading '{book title}'"
_GAVE_RE = re.compile(r"^(.+?)\s+gave\s+(\d+)\s+stars?\s+to\s+['\"](.+?)['\"]\s*$", re.IGNORECASE)
_ADDED_RE = re.compile(r"^(.+?)\s+added\s+['\"](.+?)['\"]\s*$", re.IGNORECASE)
_STARTED_RE = re.compile(r"^(.+?)\s+started reading\s+['\"](.+?)['\"]\s*$", re.IGNORECASE)
# Author extraction from description HTML:
#   `<a only_path="false" class="authorName" href="/author/show/...">Author Name</a>`
_AUTHOR_LINK_RE = re.compile(
    r'<a[^>]*class=["\']authorName["\'][^>]*>([^<]+)</a>', re.IGNORECASE
)
# Review text from description HTML:
#   The description has shelves first (`<span class="userReview">bookshelves: </span>`
#   followed by `<a class="actionLinkLite">shelf</a>` links separated by commas),
#   then a `<br/><br/>` separator, then the actual review text.
_REVIEW_BODY_RE = re.compile(
    r'<br\s*/?>\s*<br\s*/?>\s*(.*?)\s*(?:</div>|<br\s*/?>|$)',
    re.IGNORECASE | re.DOTALL,
)
# Shelves/tags:
#   `<a class="actionLinkLite" href="...?shelf=to-read">to-read</a>`
_SHELF_RE = re.compile(
    r'<a[^>]*class=["\']actionLinkLite["\'][^>]*>([^<]+)</a>', re.IGNORECASE
)


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = text.replace("&#39;", "'").replace("&apos;", "'")
    return _WS_RE.sub(" ", text).strip()


def _parse_item(title: str, description: str, guid: str, pub_date: str) -> Optional[dict]:
    """Parse one Goodreads updates RSS <item>. Returns None if it's system noise."""
    title = (title or "").strip()
    desc_html = description or ""

    # Filter out Goodreads system items — these carry no book data
    if guid.startswith("LikeOnExternalResourcePlaceholder"):
        return None

    action = None
    rating: Optional[int] = None
    book_title = ""
    name = ""

    m = _GAVE_RE.match(title)
    if m:
        name = m.group(1).strip()
        rating = int(m.group(2))
        book_title = m.group(3).strip()
        action = "rated"
    else:
        m = _STARTED_RE.match(title)
        if m:
            name = m.group(1).strip()
            book_title = m.group(2).strip()
            action = "started"
        else:
            m = _ADDED_RE.match(title)
            if m:
                name = m.group(1).strip()
                book_title = m.group(2).strip()
                action = "added"
            else:
                # Unrecognized title — could be a friend-request or status update;
                # skip rather than guess.
                return None

    author_m = _AUTHOR_LINK_RE.search(desc_html)
    author = _strip_html(author_m.group(1)) if author_m else ""

    # Pull review body — anything after the userReview shelves line, inside the
    # <br><br> block that follows the shelves list.
    review = ""
    review_m = _REVIEW_BODY_RE.search(desc_html)
    if review_m:
        review = _strip_html(review_m.group(1))

    shelves = [s.strip() for s in _SHELF_RE.findall(desc_html) if s.strip()]

    return {
        "action": action,
        "rating": rating,
        "book_title": book_title,
        "author": author,
        "review": review,
        "shelves": shelves,
        "pub_date": (pub_date or "").strip(),
        "guid": (guid or "").strip(),
    }


def _parse_rss(xml_text: str) -> tuple[list[dict], str]:
    """Parse Goodreads updates RSS. Returns (items, display_name)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], ""

    # Display name comes from <channel><title> like "Otis's Updates"
    channel_title_el = root.find("./channel/title")
    display_name = ""
    if channel_title_el is not None and channel_title_el.text:
        m = re.match(r"(.+?)['\u2019]?s?\s+Updates", channel_title_el.text.strip(), re.IGNORECASE)
        if m:
            display_name = m.group(1).strip()

    items: list[dict] = []
    for item in root.iter("item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        guid_el = item.find("guid")
        pub_el = item.find("pubDate")

        # The title and description can be wrapped in CDATA; ElementTree
        # already strips those for us.
        title_text = (title_el.text or "") if title_el is not None else ""
        # Multiple <title>/<description> children can exist (different namespaces);
        # we already iterated past .find() to first hit.
        # For namespaced duplicates we fall back to the first text content.

        guid_text = (guid_el.text or "") if guid_el is not None else ""
        pub_text = (pub_el.text or "") if pub_el is not None else ""

        parsed = _parse_item(title_text, desc_el.text or "" if desc_el is not None else "", guid_text, pub_text)
        if parsed:
            items.append(parsed)
    return items, display_name


def _build_raw_content(user_id: str, items: list[dict], display_name: str) -> str:
    """Assemble the raw_content blob from parsed items."""
    parts: list[str] = [f"[Goodreads profile] {user_id}"]
    if display_name:
        parts.append(f"  Display name: {display_name}")

    if not items:
        parts.append("  (No recent updates — RSS feed is empty)")
        return "\n".join(parts)

    # 5-star reads — strongest taste signal
    five_star = [it for it in items if it["action"] == "rated" and it.get("rating") == 5]
    if five_star:
        parts.append(f"\n[5-star reads ({len(five_star)} total)]")
        for it in five_star[:15]:
            head = f"  {it['book_title']}"
            if it.get("author"):
                head += f" by {it['author']}"
            parts.append(head)

    # All rated books
    rated = [it for it in items if it["action"] == "rated"]
    if rated:
        parts.append(f"\n[All rated books ({len(rated)} in feed)]")
        # Sort by rating desc, then by date desc
        rated_sorted = sorted(rated, key=lambda it: (-(it.get("rating") or 0), it.get("pub_date") or ""))
        for it in rated_sorted[:25]:
            line = f"  {it['book_title']}"
            if it.get("author"):
                line += f" by {it['author']}"
            if it.get("rating") is not None:
                line += f" — {it['rating']}★"
            parts.append(line)

    # Books with written reviews — highest-signal taste data.
    # An item qualifies only if it has actual review text past the shelves list,
    # NOT just a stray shelf tag captured by the actionLinkLite regex.
    reviewed = [it for it in items if it.get("review") and it["review"] not in it.get("shelves", [])]
    if reviewed:
        parts.append(f"\n[Written reviews ({len(reviewed)} total)]")
        for it in reviewed[:15]:
            head = f"  {it['book_title']}"
            if it.get("author"):
                head += f" by {it['author']}"
            if it.get("rating") is not None:
                head += f" — {it['rating']}★"
            parts.append(head)
            review = it["review"]
            if len(review) > 300:
                review = review[:297] + "..."
            parts.append(f"    \"{review}\"")

    # Currently reading / started — temporal context
    started = [it for it in items if it["action"] == "started"]
    if started:
        parts.append(f"\n[Started reading ({len(started)} in feed)]")
        for it in started[:10]:
            line = f"  {it['pub_date']} — {it['book_title']}"
            if it.get("author"):
                line += f" by {it['author']}"
            parts.append(line)

    # Recently added (to a shelf, no rating yet)
    added = [it for it in items if it["action"] == "added"]
    if added:
        parts.append(f"\n[Added to shelves ({len(added)} in feed)]")
        for it in added[:10]:
            line = f"  {it['pub_date']} — {it['book_title']}"
            if it.get("author"):
                line += f" by {it['author']}"
            if it.get("shelves"):
                line += f" [shelves: {', '.join(it['shelves'][:5])}]"
            parts.append(line)

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Network
# ─────────────────────────────────────────────────────────────────────────────

_RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _fetch_rss(user_id: str) -> Optional[str]:
    """Fetch the updates RSS for a Goodreads numeric user ID."""
    if not user_id or not user_id.isdigit():
        return None
    url = f"{GOODREADS_BASE}/user/updates_rss/{user_id}"
    try:
        async with GOODREADS_LIMITER:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=20.0, headers=_RSS_HEADERS).get(url),
                max_retries=3,
                base_delay=2.0,
            )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text
    except (httpx.HTTPError, asyncio.TimeoutError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_goodreads(user_id: str, entity_type: str = "People") -> Tuple[str, list[dict]]:
    """Full Goodreads scrape pipeline for a profile's goodreads_user_id field.

    user_id must be the numeric Goodreads user ID (visible in profile URL).
    Returns (raw_content, posts). On 404 / non-numeric / parse failure returns
    a minimal identity blob.
    """
    cached = get_goodreads_cache(user_id, entity_type)
    if cached:
        return cached, [{"source": "goodreads", "cached": True, "user_id": user_id}]

    if not user_id or not user_id.isdigit():
        raw = f"[Goodreads profile] {user_id}\n  (Invalid user ID — must be numeric)"
        save_goodreads_cache(user_id, entity_type, raw, _goodreads_source_url(user_id))
        return raw, [{"source": "goodreads", "cached": False, "user_id": user_id, "error": "invalid_id"}]

    xml_text = await _fetch_rss(user_id)
    if not xml_text:
        raw = f"[Goodreads profile] {user_id}\n  (Could not fetch updates RSS)"
        save_goodreads_cache(user_id, entity_type, raw, _goodreads_source_url(user_id))
        return raw, [{"source": "goodreads", "cached": False, "user_id": user_id, "error": "fetch_failed"}]

    items, display_name = _parse_rss(xml_text)
    raw = _build_raw_content(user_id, items, display_name)

    if len(raw) > settings.content_max_chars:
        raw = raw[: settings.content_max_chars]

    save_goodreads_cache(user_id, entity_type, raw, _goodreads_source_url(user_id))
    return raw, [{"source": "goodreads", "cached": False, "user_id": user_id, "items": len(items)}]


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from scraped Goodreads content via LiteLLM."""
    import json
    import os
    import re

    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given book-reading facts about a person named "{name}", generate exactly 50 trivia questions about their reading taste.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions should be about favorite books, authors, ratings, reading habits, and review snippets
- correct_answer and wrong_answers must be full sentences or specific facts
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: the exact phrase from the input that inspired this question (max 20 words)
- Return ONLY the JSON array, no commentary
- If you cannot generate a question for a category, skip it"""

    user_prompt = f"Book-reading facts about {name}:\n{raw_content[: settings.content_max_chars]}"

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
        print(f"Error generating goodreads questions: {e}")
        return []
