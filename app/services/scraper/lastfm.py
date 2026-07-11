"""
last.fm scraper for PEOPLE entity type.
Scrapes top artists/tracks/albums (for a given period) and recent scrobbles
via the free last.fm API. Richest structured source per request — free API
key, generous rate limits.
Rate-limit aware: uses LASTFM_LIMITER.
Cache-aware: checks/writes entity_cache before/after scrape.
"""
import httpx, json, os, re
from datetime import datetime, timezone
from typing import Optional, Tuple
from app.config import settings
from app.services.scraper.rate_limiter import LASTFM_LIMITER, retry_with_backoff
from app.database import SessionLocal, EntityCache

LASTFM_API_BASE = "http://ws.audioscrobbler.com/2.0/"
LASTFM_SOURCE_PREFIX = "https://www.last.fm/user/"

VALID_PERIODS = {"overall", "7day", "1month", "3month", "6month", "12month"}


def _lastfm_source_url(username: str) -> str:
    return f"{LASTFM_SOURCE_PREFIX}{username}"


# ─────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────

def get_lastfm_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing last.fm content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{LASTFM_SOURCE_PREFIX}%"),
        ).first()
        if cached:
            return cached.raw_content
    finally:
        db.close()
    return None


def save_lastfm_cache(entity_name: str, entity_type: str, content: str, source_url: str):
    """Save scraped last.fm content to entity_cache."""
    db = SessionLocal()
    try:
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{LASTFM_SOURCE_PREFIX}%"),
        ).first()
        if existing:
            existing.raw_content = content
            existing.scraped_at = int(datetime.now(timezone.utc).timestamp())
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


# ─────────────────────────────────────────────────────────────────
# Core scraping
# ─────────────────────────────────────────────────────────────────

async def _lastfm_get(method: str, username: str, api_key: str, extra: dict | None = None) -> dict:
    """Call a last.fm API method for a user. Returns the parsed JSON body (may contain 'error')."""
    params = {"method": method, "user": username, "api_key": api_key, "format": "json"}
    if extra:
        params.update(extra)
    async with LASTFM_LIMITER:
        resp = await retry_with_backoff(
            lambda: httpx.AsyncClient(timeout=30.0).get(LASTFM_API_BASE, params=params),
            max_retries=3,
            base_delay=1.5,
        )
        resp.raise_for_status()
        return resp.json()


async def _get_user_info(username: str, api_key: str) -> dict:
    try:
        data = await _lastfm_get("user.getinfo", username, api_key)
        return data.get("user", {}) if "error" not in data else {}
    except Exception:
        return {}


async def _get_top_artists(username: str, api_key: str, period: str, limit: int = 20) -> list[dict]:
    try:
        data = await _lastfm_get(
            "user.gettopartists", username, api_key, {"period": period, "limit": limit}
        )
        if "error" in data:
            return []
        return data.get("topartists", {}).get("artist", [])
    except Exception:
        return []


async def _get_top_tracks(username: str, api_key: str, period: str, limit: int = 20) -> list[dict]:
    try:
        data = await _lastfm_get(
            "user.gettoptracks", username, api_key, {"period": period, "limit": limit}
        )
        if "error" in data:
            return []
        return data.get("toptracks", {}).get("track", [])
    except Exception:
        return []


async def _get_top_albums(username: str, api_key: str, period: str, limit: int = 20) -> list[dict]:
    try:
        data = await _lastfm_get(
            "user.gettopalbums", username, api_key, {"period": period, "limit": limit}
        )
        if "error" in data:
            return []
        return data.get("topalbums", {}).get("album", [])
    except Exception:
        return []


async def _get_recent_tracks(username: str, api_key: str, limit: int = 10) -> list[dict]:
    try:
        data = await _lastfm_get(
            "user.getrecenttracks", username, api_key, {"limit": limit}
        )
        if "error" in data:
            return []
        return data.get("recenttracks", {}).get("track", [])
    except Exception:
        return []


def _period_label(period: str) -> str:
    return {
        "overall": "all-time",
        "7day": "last 7 days",
        "1month": "last month",
        "3month": "last 3 months",
        "6month": "last 6 months",
        "12month": "last 12 months",
    }.get(period, period)


def _build_raw_content(
    username: str,
    period: str,
    user_info: dict,
    top_artists: list[dict],
    top_tracks: list[dict],
    top_albums: list[dict],
    recent_tracks: list[dict],
) -> str:
    """Assemble the raw_content blob."""
    parts = [f"[last.fm profile] {username}"]

    if user_info:
        real_name = user_info.get("realname", "")
        if real_name:
            parts.append(f"  Real name: {real_name}")
        playcount = user_info.get("playcount", "")
        if playcount:
            parts.append(f"  Total scrobbles: {playcount}")
        country = user_info.get("country", "")
        if country and country != "None":
            parts.append(f"  Country: {country}")
        registered = user_info.get("registered", {})
        if isinstance(registered, dict) and registered.get("unixtime"):
            try:
                reg_year = datetime.fromtimestamp(int(registered["unixtime"]), tz=timezone.utc).year
                parts.append(f"  Registered since: {reg_year}")
            except (ValueError, OSError):
                pass

    label = _period_label(period)

    if top_artists:
        parts.append(f"\n[Top artists — {label}]")
        for a in top_artists:
            name = a.get("name", "")
            plays = a.get("playcount", "0")
            parts.append(f"  {name} — {plays} plays")

    if top_tracks:
        parts.append(f"\n[Top tracks — {label}]")
        for t in top_tracks:
            name = t.get("name", "")
            artist = t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else ""
            plays = t.get("playcount", "0")
            parts.append(f"  {name} by {artist} — {plays} plays" if artist else f"  {name} — {plays} plays")

    if top_albums:
        parts.append(f"\n[Top albums — {label}]")
        for al in top_albums:
            name = al.get("name", "")
            artist = al.get("artist", {}).get("name", "") if isinstance(al.get("artist"), dict) else ""
            plays = al.get("playcount", "0")
            parts.append(f"  {name} by {artist} — {plays} plays" if artist else f"  {name} — {plays} plays")

    if recent_tracks:
        parts.append(f"\n[Recent scrobbles]")
        for t in recent_tracks:
            name = t.get("name", "")
            artist = t.get("artist", {}).get("#text", "") if isinstance(t.get("artist"), dict) else ""
            now_playing = isinstance(t.get("@attr"), dict) and t["@attr"].get("nowplaying") == "true"
            suffix = " (now playing)" if now_playing else ""
            parts.append(f"  {name} by {artist}{suffix}" if artist else f"  {name}{suffix}")

    return "\n".join(parts)


async def scrape_lastfm(
    username: str, entity_type: str = "People", period: str = "overall"
) -> Tuple[str, list[dict]]:
    """
    Full last.fm scrape pipeline for a profile's lastfm_username field.
    Fetches top artists/tracks/albums for `period` plus recent scrobbles.
    Returns (raw_content, posts) where posts is a metadata dict.

    Fallback (no API key): returns a minimal identity blob without raising.
    """
    if period not in VALID_PERIODS:
        period = "overall"

    cached = get_lastfm_cache(username, entity_type)
    if cached:
        return cached, [{"source": "lastfm", "cached": True}]

    api_key = settings.lastfm_api_key

    # ── No-key fallback: identity blob only, no network call ────────────────
    if not api_key:
        raw = f"[last.fm profile] {username}\n  (No API key — limited data)"
        save_lastfm_cache(username, entity_type, raw, _lastfm_source_url(username))
        return raw, [{"source": "lastfm", "cached": False}]

    # ── Full pipeline ─────────────────────────────────────────────────────
    user_info = await _get_user_info(username, api_key)
    top_artists = await _get_top_artists(username, api_key, period)
    top_tracks = await _get_top_tracks(username, api_key, period)
    top_albums = await _get_top_albums(username, api_key, period)
    recent_tracks = await _get_recent_tracks(username, api_key)

    raw = _build_raw_content(
        username, period, user_info, top_artists, top_tracks, top_albums, recent_tracks
    )

    if len(raw) > settings.content_max_chars:
        raw = raw[: settings.content_max_chars]

    save_lastfm_cache(username, entity_type, raw, _lastfm_source_url(username))

    return raw, [{"source": "lastfm", "cached": False}]


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from scraped last.fm content via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given music-listening facts about a person named "{name}", generate exactly 50 trivia questions about their music taste.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions should be about favorite artists, tracks, albums, and listening habits
- correct_answer and wrong_answers must be full sentences or specific facts
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: the exact phrase from the input that inspired this question (max 20 words)
- Return ONLY the JSON array, no commentary
- If you cannot generate a question for a category, skip it"""

    user_prompt = f"Music facts about {name}:\n{raw_content[: settings.content_max_chars]}"

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
        print(f"Error generating last.fm questions: {e}")
        return []
