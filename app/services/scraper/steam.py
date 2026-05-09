"""
Steam scraper for PEOPLE entity type.

Uses the Steam Web API (requires free API key from steamcommunity.com/dev/apikey)
to fetch player summaries, owned games, and recently played games.

Rate-limit aware via STEAM_API_LIMITER / STEAM_STORE_LIMITER / STEAM_COMMUNITY_LIMITER.
Cache-aware via entity_cache (checks/writes before/after scrape).
"""
import asyncio, httpx, json, os, re, xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

from app.config import settings
from app.services.scraper.rate_limiter import (
    STEAM_API_LIMITER,
    STEAM_STORE_LIMITER,
    STEAM_COMMUNITY_LIMITER,
    retry_with_backoff,
)
from app.database import SessionLocal, EntityCache

STEAM_API_BASE = "https://api.steampowered.com"
STEAM_STORE_BASE = "https://store.steampowered.com"
STEAM_COMMUNITY_BASE = "https://steamcommunity.com"
STEAM_SOURCE_PREFIX = "https://steamcommunity.com/"

# Numeric SteamID64 pattern
STEAM_ID64_RE = re.compile(r"^7656119\d{10}$")

def get_steam_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{STEAM_SOURCE_PREFIX}%"),
        ).first()
        return cached.raw_content if cached else None
    finally:
        db.close()


def save_steam_cache(entity_name: str, entity_type: str, content: str):
    db = SessionLocal()
    try:
        source_url = f"{STEAM_SOURCE_PREFIX}id/{entity_name}"
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{STEAM_SOURCE_PREFIX}%"),
        ).first()
        if existing:
            existing.raw_content = content
            existing.scraped_at = int(datetime.utcnow().timestamp())
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
    """Expand K/M suffixes."""
    s = s.strip().upper()
    try:
        if s.endswith("M"):
            return str(int(float(s[:-1]) * 1_000_000))
        if s.endswith("K"):
            return str(int(float(s[:-1]) * 1_000))
    except ValueError:
        pass
    return s.replace(",", "")


# ---------------------------------------------------------------------------
# SteamID resolution
# ---------------------------------------------------------------------------

def resolve_steam_id(raw: str) -> str | None:
    """
    Resolve a freeform steam_id field to a SteamID64.

    Handles:
    - 17-digit numeric SteamID64 (already resolved)
    - steamcommunity.com URL (strip prefix)
    - vanity slug (resolve via XML endpoint)
    Returns None if resolution fails.
    """
    raw = raw.strip().strip("/")
    # Already numeric SteamID64
    if STEAM_ID64_RE.match(raw):
        return raw
    # URL — extract the trailing component
    if raw.startswith("https://steamcommunity.com/"):
        raw = raw.replace("https://steamcommunity.com/", "")
        if raw.startswith("id/"):
            raw = raw[3:]
        elif raw.startswith("profiles/"):
            raw = raw[9:]
    if raw.startswith("id/") or raw.startswith("profiles/"):
        raw = raw.split("/")[0].replace("id/", "").replace("profiles/", "")
    # Now we have a vanity slug (or a SteamID64 that slipped through numeric check)
    if STEAM_ID64_RE.match(raw):
        return raw
    # Vanity — resolve via XML endpoint
    return _resolve_vanity(raw)


async def _resolve_vanity_http(slug: str) -> str | None:
    """HTTP GET to resolve vanity slug to SteamID64."""
    url = f"{STEAM_COMMUNITY_BASE}/id/{slug}/?xml=1"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            async with STEAM_COMMUNITY_LIMITER:
                resp = await client.get(url)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            steam_id = root.find("steamID64")
            if steam_id is not None and STEAM_ID64_RE.match(steam_id.text.strip()):
                return steam_id.text.strip()
    except Exception:
        pass
    return None


def _resolve_vanity(slug: str) -> str | None:
    """Sync wrapper — runs _resolve_vanity_http via asyncio."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(_resolve_vanity_http(slug))


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

async def _api_get(url: str, limiter=None, params: dict | None = None) -> dict:
    """GET a Steam Web API endpoint with rate limiting and backoff."""
    async def _call():
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp

    if limiter:
        async with limiter:
            resp = await retry_with_backoff(_call)
    else:
        resp = await retry_with_backoff(_call)
    return resp.json()


async def _get_player_summaries(steam_id: str) -> dict:
    """GetPlayerSummaries — basic profile info."""
    if not settings.steam_api_key:
        return {}
    url = f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v0002/"
    return await _api_get(url, STEAM_API_LIMITER, {
        "key": settings.steam_api_key,
        "steamids": steam_id,
    })


async def _get_owned_games(steam_id: str) -> dict:
    """GetOwnedGames with appinfo + free games."""
    if not settings.steam_api_key:
        return {}
    url = f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v0002/"
    return await _api_get(url, STEAM_API_LIMITER, {
        "key": settings.steam_api_key,
        "steamid": steam_id,
        "include_appinfo": 1,
        "include_played_free_games": 1,
    })


async def _get_recently_played(steam_id: str) -> dict:
    """GetRecentlyPlayedGames — top 4 recent games."""
    if not settings.steam_api_key:
        return {}
    url = f"{STEAM_API_BASE}/IPlayerService/GetRecentlyPlayedGames/v0002/"
    return await _api_get(url, STEAM_API_LIMITER, {
        "key": settings.steam_api_key,
        "steamid": steam_id,
        "count": 4,
    })


async def _get_app_details_batch(app_ids: list[str]) -> dict:
    """
    GET store.steampowered.com/api/appdetails for a batch of appids.
    No auth. Rate-limited via STEAM_STORE_LIMITER.
    """
    if not app_ids:
        return {}
    csv = ",".join(app_ids)
    url = f"{STEAM_STORE_BASE}/api/appdetails"
    async def _call():
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params={"appids": csv})
            resp.raise_for_status()
            return resp
    async with STEAM_STORE_LIMITER:
        resp = await retry_with_backoff(_call)
    return resp.json()


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------

async def scrape_steam(raw_steam_id: str, entity_type: str = "People") -> tuple[str, dict]:
    """
    Scrape a Steam profile via Web API.

    Full path (with steam_api_key):
      1. resolve raw_steam_id → SteamID64
      2. GetPlayerSummaries
      3. GetOwnedGames
      4. GetRecentlyPlayedGames
      5. appdetails for top 30 games by playtime (batch 20/call)
      6. build raw_content blob

    Without steam_api_key: only resolve + XML identity blob (no games).
    Never raises; graceful sentinel on any failure.
    """
    raw_steam_id = raw_steam_id.strip().strip("/")
    if not raw_steam_id:
        return "[Steam: empty steam_id]", {}

    # Check cache
    cached = get_steam_cache(raw_steam_id, entity_type)
    if cached:
        return cached, {"source": "steam", "cached": True}

    # Resolve to SteamID64
    steam_id = resolve_steam_id(raw_steam_id)
    if not steam_id:
        return f"[Steam: could not resolve '{raw_steam_id}' — check the steam_id format]", {}

    # Fetch profile data
    profile = {
        "steam_id": steam_id,
        "persona_name": "",
        "profile_url": f"https://steamcommunity.com/profiles/{steam_id}",
        "avatar_url": "",
        "followers": "",
        "levels": [],
        "recent_games": [],
        "owned_games_count": 0,
    }

    try:
        # GetPlayerSummaries
        summaries = await _get_player_summaries(steam_id)
        if summaries:
            players = summaries.get("response", {}).get("players", [])
            if players:
                p = players[0]
                profile["persona_name"] = p.get("personaname", "")
                profile["avatar_url"] = p.get("avatarfull", "")
                profile["profile_url"] = p.get("profileurl", profile["profile_url"])
    except Exception:
        pass

    if not profile["persona_name"]:
        profile["persona_name"] = raw_steam_id

    # GetOwnedGames
    owned_raw = await _get_owned_games(steam_id)
    games = []
    if owned_raw:
        response = owned_raw.get("response", {})
        profile["owned_games_count"] = response.get("game_count", 0)
        games = response.get("games", [])
        # Sort by playtime desc
        games.sort(key=lambda g: g.get("playtime_forever", 0), reverse=True)
        games = games[:30]  # top 30 for enrichment

    # GetRecentlyPlayedGames
    try:
        recent = await _get_recently_played(steam_id)
        if recent:
            profile["recent_games"] = [
                {"name": g["name"], "playtime_2weeks": g.get("playtime_2weeks", 0)}
                for g in recent.get("response", {}).get("games", [])
            ]
    except Exception:
        pass

    # Enrich with appdetails (top games only — 2 batches of 20)
    top_app_ids = [g["appid"] for g in games[:30]]
    app_info_map = {}

    for batch in [top_app_ids[:20], top_app_ids[20:30]]:
        if not batch:
            continue
        details = await _get_app_details_batch([str(a) for a in batch])
        for appid_str, info in details.items():
            if isinstance(info, dict) and info.get("success"):
                data = info.get("data", {})
                app_info_map[int(appid_str)] = {
                    "name": data.get("name", ""),
                    "genres": [g["description"] for g in data.get("genres", [])],
                    "metacritic": data.get("metacritic", {}).get("score"),
                    "type": data.get("type", ""),
                }

    # Build levels (playtime buckets) from top 30 enriched games
    levels = []
    if games:
        for g in games[:20]:
            appid = g["appid"]
            info = app_info_map.get(appid, {})
            playtime_h = g.get("playtime_forever", 0)
            if playtime_h >= 1000:
                tier = "1k+ hours"
            elif playtime_h >= 500:
                tier = "500+ hours"
            elif playtime_h >= 200:
                tier = "200+ hours"
            elif playtime_h >= 50:
                tier = "50+ hours"
            elif playtime_h >= 10:
                tier = "10+ hours"
            else:
                tier = "<10 hours"
            levels.append({
                "appid": appid,
                "name": info.get("name", g.get("name", f"appid {appid}")),
                "playtime_h": playtime_h,
                "tier": tier,
                "genres": info.get("genres", []),
                "metacritic": info.get("metacritic"),
            })

    profile["levels"] = levels

    raw = _format_steam_profile(profile)
    raw = raw[: settings.content_max_chars]

    if raw and not raw.startswith("[Steam:"):
        save_steam_cache(raw_steam_id, entity_type, raw)

    return raw, profile


def _format_steam_profile(profile: dict) -> str:
    lines = [f"[Steam profile: {profile['persona_name']}]"]
    lines.append(f"Profile: {profile['persona_name']}")
    if profile.get("profile_url"):
        lines.append(f"URL: {profile['profile_url']}")
    if profile.get("owned_games_count"):
        lines.append(f"{profile['owned_games_count']} games in library")
    if profile.get("levels"):
        lines.append("\n[Top games by playtime]")
        for g in profile["levels"][:15]:
            genre_str = f" ({', '.join(g['genres'])})" if g['genres'] else ""
            meta_str = f" | metacritic {g['metacritic']}" if g['metacritic'] else ""
            lines.append(f"  {g['name']}{genre_str} — {g['playtime_h']}h ({g['tier']}){meta_str}")
    if profile.get("recent_games"):
        lines.append("\n[Recently played]")
        for g in profile["recent_games"]:
            lines.append(f"  {g['name']} — {g.get('playtime_2weeks', 0)}h in last 2 weeks")
    return "\n".join(lines)


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from Steam profile via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly 25 trivia questions about their personality and interests based on their Steam library data.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions are about what you can infer about the person's personality from their Steam library: games played, hours invested, genres preferred
- Steam library is the "personality jackpot" — use hours-played as a signal of identity, not just trivia
- Playtime tiers (e.g. "1000+ hours", "500+ hours") are personality indicators; use them as conversation starters
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: exact phrase from the profile (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {name} from their Steam profile:\n{raw_content[:settings.content_max_chars]}"

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
            questions = json.loads(content)
            return questions
    except Exception as e:
        print(f"Error generating Steam questions: {e}")
        return []