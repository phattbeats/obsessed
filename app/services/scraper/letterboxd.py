"""
Letterboxd scraper for PEOPLE entity type.
Scrapes a public Letterboxd diary RSS feed (no auth) for recent films watched,
ratings, rewatches, and review snippets. Highest-signal source for film taste.
Rate-limit aware: uses LETTERBOXD_LIMITER.
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
from app.services.scraper.rate_limiter import LETTERBOXD_LIMITER, retry_with_backoff

LETTERBOXD_BASE = "https://letterboxd.com"
LETTERBOXD_SOURCE_PREFIX = f"{LETTERBOXD_BASE}/"

# XML namespaces used by the Letterboxd diary RSS feed. Both must be registered
# before findall()/iter() — ElementTree doesn't do prefix-less lookups for
# namespaced elements without this.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "letterboxd": "https://letterboxd.com",
    "tmdb": "https://themoviedb.org",
}


def _letterboxd_source_url(username: str) -> str:
    return f"{LETTERBOXD_BASE}/{username}/"


# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_letterboxd_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing Letterboxd content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{LETTERBOXD_SOURCE_PREFIX}%"),
        ).first()
        if cached:
            return cached.raw_content
    finally:
        db.close()
    return None


def save_letterboxd_cache(entity_name: str, entity_type: str, content: str, source_url: str) -> None:
    """Save scraped Letterboxd content to entity_cache."""
    db = SessionLocal()
    try:
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{LETTERBOXD_SOURCE_PREFIX}%"),
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

# Title format: "Trainspotting, 1996 - ★★★★½"
_TITLE_STARS_RE = re.compile(r"★+½?$")
# Description contains "Otis gave 5 stars to ..." style HTML, but Letterboxd
# just embeds a poster <img> + optional review text in <p> tags.
_IMG_TAG_RE = re.compile(r"<img[^>]*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    """Remove HTML tags, collapse whitespace, return plain text."""
    if not text:
        return ""
    # Drop the poster img block entirely — we already have the title.
    text = _IMG_TAG_RE.sub("", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return _WS_RE.sub(" ", text).strip()


def _parse_review_text(description_html: str) -> str:
    """Extract just the user's written review from a Letterboxd item description.

    Letterboxd wraps the poster `<img>` in a `<p>` of its own, so we have to
    skip paragraphs that are img-only. The actual user review, if any, lives
    in a later `<p>` block that has text after stripping tags.
    """
    if not description_html:
        return ""
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", description_html, re.IGNORECASE | re.DOTALL):
        inner = m.group(1)
        # Skip paragraphs that are just the poster image
        if re.fullmatch(r"\s*<img[^>]*/?\s*>\s*", inner.strip(), re.IGNORECASE):
            continue
        text = _strip_html(inner)
        if text:
            return text
    return ""


def _star_glyphs_to_rating(stars_text: str) -> Optional[float]:
    """Convert "★★★★½" to 4.5. Returns None if no stars."""
    if not stars_text:
        return None
    full = stars_text.count("★")
    half = stars_text.count("½")
    if full == 0 and half == 0:
        return None
    return float(full) + (0.5 if half else 0.0)


def _parse_rss(xml_text: str) -> list[dict]:
    """Parse a Letterboxd diary RSS XML into a list of normalized items."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items: list[dict] = []
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        desc_el = item.find("description")

        # Namespaced fields — Letterboxd uses {https://letterboxd.com}...
        watched_el = item.find("letterboxd:watchedDate", _NS)
        rewatch_el = item.find("letterboxd:rewatch", _NS)
        film_title_el = item.find("letterboxd:filmTitle", _NS)
        film_year_el = item.find("letterboxd:filmYear", _NS)
        rating_el = item.find("letterboxd:memberRating", _NS)
        like_el = item.find("letterboxd:memberLike", _NS)
        tmdb_el = item.find("tmdb:movieId", _NS)

        raw_title = (title_el.text or "").strip() if title_el is not None else ""
        # Title format: "{filmTitle}, {year} - {stars}"
        title_match = re.match(r"^(.*?)(?:,\s*(\d{4}))?\s*-\s*(★+½?)\s*$", raw_title)
        if title_match:
            film_title = title_match.group(1).strip()
            year = title_match.group(2) or ""
            stars_text = title_match.group(3)
            rating = _star_glyphs_to_rating(stars_text)
        else:
            film_title = film_title_el.text.strip() if film_title_el is not None and film_title_el.text else raw_title
            year = film_year_el.text.strip() if film_year_el is not None and film_year_el.text else ""
            rating = None
            try:
                if rating_el is not None and rating_el.text:
                    rating = float(rating_el.text)
            except (ValueError, TypeError):
                rating = None

        watched_date = (
            watched_el.text.strip()
            if watched_el is not None and watched_el.text
            else ""
        )
        is_rewatch = (
            rewatch_el is not None and (rewatch_el.text or "").strip().lower() == "yes"
        )
        is_like = (
            like_el is not None and (like_el.text or "").strip().lower() == "yes"
        )
        tmdb_id = (
            tmdb_el.text.strip() if tmdb_el is not None and tmdb_el.text else ""
        )
        review = _parse_review_text(desc_el.text or "" if desc_el is not None else "")
        pub_date = (pub_el.text or "").strip() if pub_el is not None else ""

        items.append({
            "film_title": film_title,
            "year": year,
            "rating": rating,
            "watched_date": watched_date,
            "is_rewatch": is_rewatch,
            "is_like": is_like,
            "review": review,
            "tmdb_id": tmdb_id,
            "pub_date": pub_date,
            "link": (link_el.text or "").strip() if link_el is not None else "",
        })
    return items


def _build_raw_content(username: str, items: list[dict], page_title: str) -> str:
    """Assemble the raw_content blob from parsed items."""
    parts: list[str] = [f"[Letterboxd profile] {username}"]
    if page_title and page_title.strip() and page_title.strip() != f"Letterboxd - {username}":
        # Page title sometimes embeds the display name
        m = re.match(r"Letterboxd\s*-\s*(.+)", page_title.strip())
        if m and m.group(1).strip().lower() != username.lower():
            parts.append(f"  Display name: {m.group(1).strip()}")

    if not items:
        parts.append("  (No diary entries found)")
        return "\n".join(parts)

    # Sort by rating desc, then by date desc — high-rated + recent first
    def _sort_key(it: dict) -> tuple:
        return (-(it.get("rating") or 0.0), it.get("watched_date") or "")

    rated = [it for it in items if it.get("rating") is not None]
    rated.sort(key=_sort_key)
    top_rated = rated[:20]

    parts.append(f"\n[Top-rated films ({len(rated)} rated total)]")
    for it in top_rated:
        stars = "★" * int(it["rating"]) + ("½" if it["rating"] % 1 else "")
        title_line = f"  {it['film_title']}"
        if it.get("year"):
            title_line += f" ({it['year']})"
        title_line += f" — {stars}"
        if it.get("is_rewatch"):
            title_line += " (rewatch)"
        parts.append(title_line)

    # Films with written reviews — these are the highest-signal taste data
    reviewed = [it for it in items if it.get("review")]
    if reviewed:
        parts.append(f"\n[Written reviews ({len(reviewed)} total)]")
        for it in reviewed[:15]:
            head = f"  {it['film_title']}"
            if it.get("year"):
                head += f" ({it['year']})"
            if it.get("rating") is not None:
                stars = "★" * int(it["rating"]) + ("½" if it["rating"] % 1 else "")
                head += f" — {stars}"
            parts.append(head)
            # Truncate long reviews
            review = it["review"]
            if len(review) > 300:
                review = review[:297] + "..."
            parts.append(f"    \"{review}\"")

    # Recent watches — temporal context
    parts.append("\n[Recent watches]")
    for it in items[:10]:
        date = it.get("watched_date") or it.get("pub_date") or ""
        head = f"  {date} — {it['film_title']}"
        if it.get("year"):
            head += f" ({it['year']})"
        if it.get("rating") is not None:
            stars = "★" * int(it["rating"]) + ("½" if it["rating"] % 1 else "")
            head += f" {stars}"
        if it.get("is_rewatch"):
            head += " [rewatch]"
        parts.append(head)

    # Liked films — quick taste signal
    liked = [it for it in items if it.get("is_like")]
    if liked:
        parts.append(f"\n[Films marked as liked: {len(liked)} total]")
        for it in liked[:10]:
            head = f"  {it['film_title']}"
            if it.get("year"):
                head += f" ({it['year']})"
            parts.append(head)

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Network
# ─────────────────────────────────────────────────────────────────────────────

# Headers that keep Cloudflare from issuing a JS challenge for the diary feed.
# Letterboxd's /rss/ endpoint works with a real desktop UA + Accept: */*. The
# /watched/ feed is gated harder, so we don't try it.
_RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _fetch_rss(username: str) -> Tuple[Optional[str], Optional[str]]:
    """Fetch the diary RSS for a username. Returns (xml_text, page_title) or (None, None)."""
    url = f"{LETTERBOXD_BASE}/{username}/rss/"
    try:
        async with LETTERBOXD_LIMITER:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=20.0, headers=_RSS_HEADERS).get(url),
                max_retries=3,
                base_delay=2.0,
            )
        if resp.status_code == 404:
            return None, None
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        title_el = root.find("./channel/title")
        page_title = (title_el.text or "").strip() if title_el is not None else ""
        return resp.text, page_title
    except (httpx.HTTPError, ET.ParseError, asyncio.TimeoutError):
        return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_letterboxd(username: str, entity_type: str = "People") -> Tuple[str, list[dict]]:
    """Full Letterboxd scrape pipeline for a profile's letterboxd_username field.

    Returns (raw_content, posts). On 404 / parse failure returns a minimal
    identity blob — caller decides whether that's a profile-not-found vs.
    transient network error.
    """
    cached = get_letterboxd_cache(username, entity_type)
    if cached:
        return cached, [{"source": "letterboxd", "cached": True, "username": username}]

    xml_text, page_title = await _fetch_rss(username)
    if not xml_text:
        raw = f"[Letterboxd profile] {username}\n  (Could not fetch diary RSS)"
        save_letterboxd_cache(username, entity_type, raw, _letterboxd_source_url(username))
        return raw, [{"source": "letterboxd", "cached": False, "username": username, "error": "fetch_failed"}]

    items = _parse_rss(xml_text)
    raw = _build_raw_content(username, items, page_title or "")

    if len(raw) > settings.content_max_chars:
        raw = raw[: settings.content_max_chars]

    save_letterboxd_cache(username, entity_type, raw, _letterboxd_source_url(username))
    return raw, [{"source": "letterboxd", "cached": False, "username": username, "items": len(items)}]


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from scraped Letterboxd content via LiteLLM.

    Imported here (rather than at module top) to avoid a circular import with
    the route module that calls us.
    """
    import json
    import os
    import re

    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given film-watching facts about a person named "{name}", generate exactly 50 trivia questions about their film taste.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions should be about favorite films, ratings, directors, years, rewatches, and review snippets
- correct_answer and wrong_answers must be full sentences or specific facts
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: the exact phrase from the input that inspired this question (max 20 words)
- Return ONLY the JSON array, no commentary
- If you cannot generate a question for a category, skip it"""

    user_prompt = f"Film-watching facts about {name}:\n{raw_content[: settings.content_max_chars]}"

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
        print(f"Error generating letterboxd questions: {e}")
        return []
