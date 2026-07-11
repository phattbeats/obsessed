"""
YouTube scraper for PEOPLE entity type.

Primary: unauthenticated Innertube JSON endpoints (same public "WEB" client
that youtube.com itself calls) — channel search, channel uploads, video
metadata. Same pattern as tiktok.py: no login, no cookies, no API key.

Fallback: YouTube Data API v3 (free 10k units/day) when INNERTUBE fails and
settings.youtube_api_key is set.

Cache-aware via entity_cache (checks/writes before/after scrape).
"""
import httpx, time
from typing import Optional

from app.config import settings
from app.services.scraper.rate_limiter import generic_limiter
from app.database import SessionLocal, EntityCache

INNERTUBE_BASE = "https://www.youtube.com/youtubei/v1"
# Public browser API key baked into every youtube.com page load — not a secret,
# not tied to any account; every Innertube client (yt-dlp, Invidious, etc) uses it.
INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
INNERTUBE_CLIENT_VERSION = "2.20240101.01.00"
# base64 protobuf selecting the channel "Videos" tab in the browse endpoint.
UPLOADS_TAB_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"

DATA_API_BASE = "https://www.googleapis.com/youtube/v3"

YOUTUBE_SOURCE_PREFIX = "https://www.youtube.com/channel/"


def _innertube_context() -> dict:
    return {"client": {"clientName": "WEB", "clientVersion": INNERTUBE_CLIENT_VERSION}}


# ---------------------------------------------------------------------------
# Cache helpers (mirrors tiktok.py)
# ---------------------------------------------------------------------------

def get_youtube_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{YOUTUBE_SOURCE_PREFIX}%"),
        ).first()
        return cached.raw_content if cached else None
    finally:
        db.close()


def save_youtube_cache(entity_name: str, entity_type: str, content: str, channel_id: str = ""):
    db = SessionLocal()
    try:
        source_url = f"{YOUTUBE_SOURCE_PREFIX}{channel_id or entity_name}"
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{YOUTUBE_SOURCE_PREFIX}%"),
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


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _text(node: Optional[dict]) -> str:
    """Extract display text from a YouTube {simpleText} or {runs:[...]} node."""
    if not node:
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    if "runs" in node:
        return "".join(r.get("text", "") for r in node["runs"])
    if "content" in node:
        return node["content"]
    return ""


def _parse_channel_renderer(cr: dict) -> dict:
    """
    Parse a search-result channelRenderer into a channel profile dict.
    NOTE: YouTube swaps these two fields' apparent meaning — videoCountText
    actually holds the subscriber count text, subscriberCountText actually
    holds the @handle. Confirmed against a live search response.
    """
    verified = any(
        b.get("metadataBadgeRenderer", {}).get("style") == "BADGE_STYLE_TYPE_VERIFIED"
        for b in cr.get("ownerBadges", []) or []
    )
    return {
        "channel_id": cr.get("channelId", ""),
        "title": _text(cr.get("title")),
        "handle": _text(cr.get("subscriberCountText")),
        "subscribers": _text(cr.get("videoCountText")),
        "description": _text(cr.get("descriptionSnippet")),
        "verified": verified,
    }


def _parse_video_renderer(vr: dict) -> dict:
    return {
        "video_id": vr.get("videoId", ""),
        "title": _text(vr.get("title")),
        "channel": _text(vr.get("longBylineText")),
        "published": _text(vr.get("publishedTimeText")),
        "length": _text(vr.get("lengthText")),
        "views": _text(vr.get("viewCountText")),
    }


def _parse_lockup_video(lockup: dict) -> dict:
    """Parse a channel-uploads-grid lockupViewModel into a video dict."""
    meta = lockup.get("metadata", {}).get("lockupMetadataViewModel", {})
    rows = meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
    parts = rows[0].get("metadataParts", []) if rows else []
    views = parts[0].get("text", {}).get("content", "") if len(parts) > 0 else ""
    published = parts[1].get("text", {}).get("content", "") if len(parts) > 1 else ""
    return {
        "video_id": lockup.get("contentId", ""),
        "title": meta.get("title", {}).get("content", ""),
        "views": views,
        "published": published,
    }


def _format_channel_block(profile: dict, uploads: list[dict]) -> str:
    lines = []
    handle = profile.get("handle", "")
    verified = " ✓" if profile.get("verified") else ""
    lines.append(f"[YouTube channel: {profile.get('title', '')}{verified}]")
    if handle:
        lines.append(f"Handle: {handle}")
    if profile.get("subscribers"):
        lines.append(f"Subscribers: {profile['subscribers']}")
    if profile.get("description"):
        lines.append(f"About: {profile['description']}")
    if uploads:
        lines.append("\n[Recent uploads]")
        for v in uploads:
            meta_bits = " — ".join(b for b in (v.get("views"), v.get("published")) if b)
            lines.append(f"  {v.get('title', '')}" + (f" ({meta_bits})" if meta_bits else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Innertube source
# ---------------------------------------------------------------------------

async def _innertube_post(endpoint: str, body: dict) -> dict:
    url = f"{INNERTUBE_BASE}/{endpoint}?key={INNERTUBE_KEY}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        async with generic_limiter:
            resp = await client.post(url, json={"context": _innertube_context(), **body})
        resp.raise_for_status()
        return resp.json()


async def search_youtube_channel_innertube(query: str) -> tuple[str, dict]:
    """Primary: Innertube search — first channelRenderer + a few videoRenderers."""
    try:
        data = await _innertube_post("search", {"query": query})
    except Exception:
        return "", {}

    try:
        contents = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [{}])[0]
            .get("itemSectionRenderer", {})
            .get("contents", [])
        )
    except Exception:
        return "", {}

    profile = {}
    videos = []
    for item in contents:
        if "channelRenderer" in item and not profile:
            profile = _parse_channel_renderer(item["channelRenderer"])
        elif "videoRenderer" in item:
            videos.append(_parse_video_renderer(item["videoRenderer"]))

    if not profile.get("channel_id"):
        return "", {}

    profile["videos"] = videos[:10]
    return _format_channel_block(profile, videos[:10]), profile


async def get_channel_uploads_innertube(channel_id: str, limit: int = 10) -> list[dict]:
    """Primary: Innertube browse on the channel's "Videos" tab."""
    try:
        data = await _innertube_post(
            "browse", {"browseId": channel_id, "params": UPLOADS_TAB_PARAMS}
        )
    except Exception:
        return []

    try:
        tabs = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
        items = tabs[1]["tabRenderer"]["content"]["richGridRenderer"]["contents"]
    except Exception:
        return []

    uploads = []
    for item in items:
        lockup = item.get("richItemRenderer", {}).get("content", {}).get("lockupViewModel")
        if lockup:
            uploads.append(_parse_lockup_video(lockup))
    return uploads[:limit]


async def get_video_metadata_innertube(video_id: str) -> dict:
    """Primary: Innertube player — videoDetails + microformat."""
    try:
        data = await _innertube_post("player", {"videoId": video_id})
    except Exception:
        return {}

    vd = data.get("videoDetails", {})
    if not vd.get("videoId"):
        return {}

    mf = data.get("microformat", {}).get("playerMicroformatRenderer", {})
    return {
        "video_id": vd.get("videoId", ""),
        "title": vd.get("title", ""),
        "channel": vd.get("author", ""),
        "channel_id": vd.get("channelId", ""),
        "length_seconds": vd.get("lengthSeconds", ""),
        "view_count": vd.get("viewCount", ""),
        "description": vd.get("shortDescription", ""),
        "publish_date": mf.get("publishDate", ""),
        "category": mf.get("category", ""),
    }


# ---------------------------------------------------------------------------
# Data API v3 fallback
# ---------------------------------------------------------------------------

async def _data_api_get(path: str, params: dict) -> dict:
    api_key = settings.youtube_api_key
    if not api_key:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            async with generic_limiter:
                resp = await client.get(
                    f"{DATA_API_BASE}/{path}", params={**params, "key": api_key}
                )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {}


async def search_youtube_channel_data_api(query: str) -> tuple[str, dict]:
    """Fallback: Data API v3 search (type=channel) + channels for stats."""
    search_data = await _data_api_get(
        "search", {"part": "snippet", "type": "channel", "q": query, "maxResults": 1}
    )
    items = search_data.get("items", [])
    if not items:
        return "", {}

    channel_id = items[0].get("snippet", {}).get("channelId") or items[0].get("id", {}).get("channelId", "")
    if not channel_id:
        return "", {}

    channel_data = await _data_api_get(
        "channels", {"part": "snippet,statistics", "id": channel_id}
    )
    ch_items = channel_data.get("items", [])
    if not ch_items:
        return "", {}

    snippet = ch_items[0].get("snippet", {})
    stats = ch_items[0].get("statistics", {})
    profile = {
        "channel_id": channel_id,
        "title": snippet.get("title", ""),
        "handle": snippet.get("customUrl", ""),
        "subscribers": stats.get("subscriberCount", ""),
        "description": snippet.get("description", ""),
        "verified": False,
        "videos": [],
    }
    return _format_channel_block(profile, []), profile


async def get_channel_uploads_data_api(channel_id: str, limit: int = 10) -> list[dict]:
    """Fallback: Data API v3 search ordered by date for a channel."""
    data = await _data_api_get(
        "search",
        {
            "part": "snippet",
            "channelId": channel_id,
            "order": "date",
            "type": "video",
            "maxResults": limit,
        },
    )
    uploads = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        uploads.append({
            "video_id": item.get("id", {}).get("videoId", ""),
            "title": snippet.get("title", ""),
            "views": "",
            "published": snippet.get("publishedAt", ""),
        })
    return uploads


async def get_video_metadata_data_api(video_id: str) -> dict:
    """Fallback: Data API v3 videos endpoint."""
    data = await _data_api_get(
        "videos", {"part": "snippet,statistics,contentDetails", "id": video_id}
    )
    items = data.get("items", [])
    if not items:
        return {}
    snippet = items[0].get("snippet", {})
    stats = items[0].get("statistics", {})
    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "channel_id": snippet.get("channelId", ""),
        "length_seconds": "",
        "view_count": stats.get("viewCount", ""),
        "description": snippet.get("description", ""),
        "publish_date": snippet.get("publishedAt", ""),
        "category": "",
    }


# ---------------------------------------------------------------------------
# Public entry points — Innertube first, Data API v3 fallback
# ---------------------------------------------------------------------------

async def search_youtube_channel(query: str) -> tuple[str, dict]:
    raw, profile = await search_youtube_channel_innertube(query)
    if raw and profile.get("channel_id"):
        return raw, profile
    return await search_youtube_channel_data_api(query)


async def get_channel_uploads(channel_id: str, limit: int = 10) -> list[dict]:
    uploads = await get_channel_uploads_innertube(channel_id, limit)
    if uploads:
        return uploads
    return await get_channel_uploads_data_api(channel_id, limit)


async def get_video_metadata(video_id: str) -> dict:
    meta = await get_video_metadata_innertube(video_id)
    if meta:
        return meta
    return await get_video_metadata_data_api(video_id)


async def scrape_youtube(handle_or_query: str, entity_type: str = "People") -> tuple[str, dict]:
    """Cache-aware wrapper, mirrors tiktok.scrape_tiktok. Feeds watched/gaming/music-video categories."""
    cached = get_youtube_cache(handle_or_query, entity_type)
    if cached:
        return cached, {"source": "youtube", "cached": True}

    raw, profile = await search_youtube_channel(handle_or_query)
    channel_id = profile.get("channel_id", "")

    if channel_id:
        uploads = await get_channel_uploads(channel_id)
        if uploads:
            profile["videos"] = uploads
            raw = _format_channel_block(profile, uploads)

    if len(raw) > settings.content_max_chars:
        raw = raw[:settings.content_max_chars]

    if not raw or not channel_id:
        return (
            f"[YouTube scrape error: all sources failed for '{handle_or_query}']",
            {},
        )

    save_youtube_cache(handle_or_query, entity_type, raw, channel_id)
    return raw, {"source": "youtube", "cached": False, **profile}


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from a YouTube channel/uploads blob via LiteLLM."""
    import json, os, re
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly 25 trivia questions about their personality and interests based on their YouTube channel data.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Favor questions about video topics, upload cadence, and channel focus (gaming, music, vlogging, etc) over literal subscriber counts
- Use recent upload titles as the strongest signal of current interests
- Wrong answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: exact phrase from the channel data (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {name} from their YouTube channel:\n{raw_content[:settings.content_max_chars]}"

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
        print(f"Error generating YouTube questions: {e}")
        return []
