"""
Steam scraper for PEOPLE entity type.
Scrapes Steam profile, owned games, recent games, and app details.
Rate-limit aware: uses STEAM_API_LIMITER / STEAM_STORE_LIMITER / STEAM_COMMUNITY_LIMITER.
Cache-aware: checks/writes entity_cache before/after scrape.
"""
import asyncio, httpx, json, os, re
from datetime import datetime
from typing import Optional, Tuple
from app.config import settings
from app.services.scraper.rate_limiter import (
    STEAM_API_LIMITER,
    STEAM_STORE_LIMITER,
    STEAM_COMMUNITY_LIMITER,
    retry_with_backoff,
)
from app.database import SessionLocal, EntityCache

STEAM_COMMUNITY_BASE = "https://steamcommunity.com"
STEAM_API_BASE = "https://api.steampowered.com"
STEAM_STORE_BASE = "https://store.steampowered.com"

STEAM_SOURCE_PREFIX = "https://steamcommunity.com/"


def _steam_source_url(steam_id: str) -> str:
    return f"{STEAM_COMMUNITY_BASE}/profiles/{steam_id}"


# ─────────────────────────────────────────────────────────────────
# SteamID resolution
# ─────────────────────────────────────────────────────────────────

STEAMID64_RE = re.compile(r"^7656119\d{10}$")


def is_steam_id64(val: str) -> bool:
    return bool(STEAMID64_RE.match(val))


async def resolve_vanity_to_id(vanity_slug: str) -> Optional[str]:
    """Resolve a vanity username to SteamID64 via the community XML feed."""
    url = f"{STEAM_COMMUNITY_BASE}/id/{vanity_slug.lstrip('/')}/?xml=1"
    try:
        async with STEAM_COMMUNITY_LIMITER:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=30.0, follow_redirects=True).get(url),
                max_retries=3,
                base_delay=2.0,
            )
            resp.raise_for_status()
            text = resp.text
        m = re.search(r"<steamID64>(\d{17})</steamID64>", text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


async def resolve_steam_id(raw: str) -> str:
    """
    Resolve a steam_id input to a SteamID64 string.
    Handles:
      - numeric SteamID64: returned unchanged
      - community URL: stripped and re-detected
      - vanity slug: resolved via XML feed
    """
    raw = raw.strip().rstrip("/")

    # Case 1: already a numeric SteamID64
    if is_steam_id64(raw):
        return raw

    # Case 2: community URL — extract the trailing component
    if raw.startswith(("http://", "https://")):
        # e.g. https://steamcommunity.com/id/karljobst/
        parts = raw.rstrip("/").split("/")
        key = parts[-1] if parts[-1] not in ("id", "profiles") else parts[-2]
        raw = key

    # Case 3: vanity slug
    sid = await resolve_vanity_to_id(raw)
    if sid:
        return sid
    # Couldn't resolve — treat the raw input as a SteamID64 and hope for the best
    return raw


# ─────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────

def get_steam_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing Steam content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{STEAM_SOURCE_PREFIX}%"),
        ).first()
        if cached:
            return cached.raw_content
    finally:
        db.close()
    return None


def save_steam_cache(entity_name: str, entity_type: str, content: str, source_url: str):
    """Save scraped Steam content to entity_cache."""
    db = SessionLocal()
    try:
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

async def _get_player_summaries(steam_id64: str, api_key: str) -> dict:
    """Fetch display name and avatar for a SteamID64."""
    if not api_key:
        return {}
    try:
        async with STEAM_API_LIMITER:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=30.0).get(
                    f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
                    params={"key": api_key, "steamids": steam_id64},
                ),
                max_retries=3,
                base_delay=1.5,
            )
            resp.raise_for_status()
            data = resp.json()
        players = data.get("response", {}).get("players", [])
        return players[0] if players else {}
    except Exception:
        return {}


async def _get_owned_games(steam_id64: str, api_key: str) -> list[dict]:
    """Fetch owned games with playtime and app info."""
    if not api_key:
        return []
    try:
        async with STEAM_API_LIMITER:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=30.0).get(
                    f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/",
                    params={
                        "key": api_key,
                        "steamid": steam_id64,
                        "include_appinfo": 1,
                        "include_played_free_games": 1,
                    },
                ),
                max_retries=3,
                base_delay=1.5,
            )
            resp.raise_for_status()
            data = resp.json()
        games = data.get("response", {}).get("games", [])
        return games if games else []
    except Exception:
        return []


async def _get_recently_played(steam_id64: str, api_key: str) -> list[dict]:
    """Fetch top 5 recently played games."""
    if not api_key:
        return []
    try:
        async with STEAM_API_LIMITER:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=30.0).get(
                    f"{STEAM_API_BASE}/IPlayerService/GetRecentlyPlayedGames/v1/",
                    params={"key": api_key, "steamid": steam_id64, "count": 5},
                ),
                max_retries=3,
                base_delay=1.5,
            )
            resp.raise_for_status()
            data = resp.json()
        games = data.get("response", {}).get("games", [])
        return games if games else []
    except Exception:
        return []


async def _get_appdetails_batch(app_ids: list[int]) -> dict[int, dict]:
    """
    Fetch app details for a batch of app IDs from the public Steam Store API.
    No auth required. Returns {appid: {...}} for apps that have a valid store page.
    """
    if not app_ids:
        return {}
    ids_csv = ",".join(str(a) for a in app_ids)
    try:
        async with STEAM_STORE_LIMITER:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=30.0).get(
                    f"{STEAM_STORE_BASE}/api/appdetails",
                    params={"appids": ids_csv, "l": "en"},
                ),
                max_retries=3,
                base_delay=2.0,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {}


def _build_raw_content(
    persona_name: str,
    summary: dict,
    owned_games: list[dict],
    recent_games: list[dict],
    appdetails_by_id: dict[int, dict],
) -> str:
    """Assemble the raw_content blob."""
    parts = [f"[Steam profile] {persona_name}"]

    if summary:
        name = summary.get("personaname") or summary.get("realname") or persona_name
        parts.append(f"  Display name: {name}")
        loc = summary.get("loccountrycode", "")
        state = summary.get("locstatecode", "")
        if loc or state:
            parts.append(f"  Location: {state},{loc}" if state else f"  Country: {loc}")
        url = summary.get("profileurl", "")
        if url:
            parts.append(f"  Profile URL: {url}")

    # Recently played
    if recent_games:
        parts.append(f"\n[Recently played games]")
        for g in recent_games[:5]:
            name = appdetails_by_id.get(g["appid"], {}).get("name", f"App {g['appid']}")
            parts.append(f"  {name} ({g.get('playtime_forever', 0)//60}h last 2 wks)")

    # Top games by all-time playtime
    if owned_games:
        sorted_games = sorted(owned_games, key=lambda g: g.get("playtime_forever", 0), reverse=True)
        top30 = sorted_games[:30]
        parts.append(f"\n[Top games by playtime]")
        for g in top30:
            name = appdetails_by_id.get(g["appid"], {}).get("name", f"App {g['appid']}")
            hours = g.get("playtime_forever", 0) // 60
            parts.append(f"  {name} — {hours}h")

    return "\n".join(parts)


async def scrape_steam(raw_input: str, entity_type: str = "People") -> Tuple[str, list[dict]]:
    """
    Full Steam scrape pipeline for a profile's steam_id field.
    Returns (raw_content, posts) where posts is a metadata dict.

    Fallback (no API key): resolves steam_id + fetches XML profile only,
    returns a minimal identity blob without raising.
    """
    # Resolve to SteamID64
    steam_id64 = await resolve_steam_id(raw_input)
    api_key = settings.steam_api_key

    # Cache check — use the resolved ID as cache key alongside entity_name
    # (steam_id on Profile is the raw input; cache by resolved ID for uniqueness)
    # We use the resolved ID for the source URL only; cache lookup uses entity_name.
    cached = get_steam_cache(raw_input, entity_type)
    if cached:
        return cached, [{"source": "steam", "cached": True}]

    # ── No-key fallback: XML identity only ──────────────────────────────
    if not api_key:
        try:
            async with STEAM_COMMUNITY_LIMITER:
                resp = await retry_with_backoff(
                    lambda: httpx.AsyncClient(timeout=30.0, follow_redirects=True).get(
                        f"{STEAM_COMMUNITY_BASE}/profiles/{steam_id64}/?xml=1"
                    ),
                    max_retries=3,
                    base_delay=2.0,
                )
                resp.raise_for_status()
                xml = resp.text
            m = re.search(r"<steamID>([^<]+)</steamID>", xml)
            steam_name = m.group(1).strip() if m else raw_input
            raw = f"[Steam profile] {steam_name}\n  SteamID64: {steam_id64}\n  (No API key — limited data)"
        except Exception:
            raw = f"[Steam profile] {raw_input}\n  SteamID64: {steam_id64}\n  (No API key — limited data)"

        save_steam_cache(raw_input, entity_type, raw, _steam_source_url(steam_id64))
        return raw, [{"source": "steam", "cached": False}]

    # ── Full pipeline ─────────────────────────────────────────────────────
    # 1. Player summaries
    summary = await _get_player_summaries(steam_id64, api_key)

    # 2. Owned games
    owned_games = await _get_owned_games(steam_id64, api_key)

    # 3. Recently played
    recent_games = await _get_recently_played(steam_id64, api_key)

    # 4. App details for top 30 owned games (batch 20-at-a-time)
    appdetails_by_id: dict[int, dict] = {}
    if owned_games:
        sorted_games = sorted(owned_games, key=lambda g: g.get("playtime_forever", 0), reverse=True)
        top_ids = [g["appid"] for g in sorted_games[:30]]
        for chunk in [top_ids[i : i + 20] for i in range(0, len(top_ids), 20)]:
            batch_result = await _get_appdetails_batch(chunk)
            for appid, info in batch_result.items():
                if isinstance(info, dict) and info.get("success"):
                    data = info.get("data", {})
                    appdetails_by_id[int(appid)] = data

    # 5. Build raw_content blob
    persona_name = (
        summary.get("personaname")
        or summary.get("realname")
        or (await resolve_vanity_to_id(raw_input) and raw_input)
        or raw_input
    )
    raw = _build_raw_content(persona_name, summary, owned_games, recent_games, appdetails_by_id)

    # Cap
    if len(raw) > settings.content_max_chars:
        raw = raw[: settings.content_max_chars]

    # Save to cache
    save_steam_cache(raw_input, entity_type, raw, _steam_source_url(steam_id64))

    return raw, [{"source": "steam", "cached": False}]


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from scraped Steam content via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly 50 trivia questions about them.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- correct_answer and wrong_answers must be full sentences or specific facts
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: the exact phrase from the input that inspired this question (max 20 words)
- Return ONLY the JSON array, no commentary
- If you cannot generate a question for a category, skip it"""

    user_prompt = f"Facts about {name}:\n{raw_content[: settings.content_max_chars]}"

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
        print(f"Error generating Steam questions: {e}")
        return []
