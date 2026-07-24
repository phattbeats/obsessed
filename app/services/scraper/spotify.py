"""
Spotify scraper for PEOPLE entity type.
Spotify's Web API has no public per-user scrape path (unlike last.fm) — a
profile's own listening data (top artists/tracks, playlists) is only
reachable once that person links their account via Authorization Code +
PKCE (see app/routes/spotify_auth.py). This module handles the token
exchange/refresh mechanics and shapes the pulled data into the same
raw_content blob shape as lastfm.py, so generate_from_manual/generate_questions
can consume it unchanged.
Rate-limit aware: uses SPOTIFY_LIMITER.
Cache-aware: checks/writes entity_cache before/after scrape, keyed by the
linked Spotify user id (not the profile's display name, since two profiles
could theoretically share a name).
"""
import base64
import hashlib
import httpx
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import urlencode
from app.config import settings
from app.services.scraper.rate_limiter import SPOTIFY_LIMITER, retry_with_backoff
from app.database import SessionLocal, EntityCache, Profile

SPOTIFY_AUTH_BASE = "https://accounts.spotify.com"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_SOURCE_PREFIX = "https://open.spotify.com/user/"
SPOTIFY_SCOPES = "user-top-read playlist-read-private playlist-read-collaborative"

VALID_TIME_RANGES = {"short_term", "medium_term", "long_term"}

# Refresh the access token this many seconds before it actually expires,
# so a slow request never races the expiry.
TOKEN_REFRESH_SKEW = 60


def _spotify_source_url(spotify_user_id: str) -> str:
    return f"{SPOTIFY_SOURCE_PREFIX}{spotify_user_id}"


# ─────────────────────────────────────────────────────────────────
# PKCE helpers
# ─────────────────────────────────────────────────────────────────

def generate_pkce_pair() -> Tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636 / Spotify's PKCE flow."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_authorize_url(state: str, code_challenge: str) -> str:
    """Build the Spotify authorize URL the browser should be redirected to."""
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "state": state,
        "scope": SPOTIFY_SCOPES,
    }
    return f"{SPOTIFY_AUTH_BASE}/authorize?{urlencode(params)}"


# ─────────────────────────────────────────────────────────────────
# Token exchange / refresh
# ─────────────────────────────────────────────────────────────────

async def exchange_code_for_token(code: str, code_verifier: str) -> dict:
    """Exchange an authorization code for access/refresh tokens. Raises on failure."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.spotify_redirect_uri,
        "client_id": settings.spotify_client_id,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{SPOTIFY_AUTH_BASE}/api/token", data=data)
        resp.raise_for_status()
        return resp.json()


async def _refresh_access_token(refresh_token: str) -> dict:
    """Use a refresh_token to obtain a fresh access_token. Raises on failure."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.spotify_client_id,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{SPOTIFY_AUTH_BASE}/api/token", data=data)
        resp.raise_for_status()
        return resp.json()


async def ensure_fresh_token(profile_id: int) -> Optional[str]:
    """
    Return a valid access token for the given profile, refreshing it first
    if it's expired (or about to). Persists a refreshed token back onto the
    Profile row. Returns None if the profile isn't linked.
    """
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p or not p.spotify_access_token:
            return None
        now = int(time.time())
        if (p.spotify_token_expires_at or 0) - TOKEN_REFRESH_SKEW > now:
            return p.spotify_access_token
        if not p.spotify_refresh_token:
            return p.spotify_access_token
        token_data = await _refresh_access_token(p.spotify_refresh_token)
        p.spotify_access_token = token_data["access_token"]
        # Spotify only returns a new refresh_token sometimes — keep the old one otherwise.
        if token_data.get("refresh_token"):
            p.spotify_refresh_token = token_data["refresh_token"]
        p.spotify_token_expires_at = now + int(token_data.get("expires_in", 3600))
        db.commit()
        return p.spotify_access_token
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────

def get_spotify_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing Spotify content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{SPOTIFY_SOURCE_PREFIX}%"),
        ).first()
        if cached:
            return cached.raw_content
    finally:
        db.close()
    return None


def save_spotify_cache(entity_name: str, entity_type: str, content: str, source_url: str):
    """Save scraped Spotify content to entity_cache."""
    db = SessionLocal()
    try:
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{SPOTIFY_SOURCE_PREFIX}%"),
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

async def _spotify_get(endpoint: str, access_token: str, params: dict | None = None) -> dict:
    """Call a Spotify Web API GET endpoint. Returns the parsed JSON body (may contain 'error')."""
    async with SPOTIFY_LIMITER:
        resp = await retry_with_backoff(
            lambda: httpx.AsyncClient(timeout=30.0).get(
                f"{SPOTIFY_API_BASE}{endpoint}",
                params=params or {},
                headers={"Authorization": f"Bearer {access_token}"},
            ),
            max_retries=3,
            base_delay=1.5,
        )
        resp.raise_for_status()
        return resp.json()


async def _get_me(access_token: str) -> dict:
    try:
        return await _spotify_get("/me", access_token)
    except Exception:
        return {}


async def _get_top_artists(access_token: str, time_range: str, limit: int = 20) -> list[dict]:
    try:
        data = await _spotify_get(
            "/me/top/artists", access_token, {"time_range": time_range, "limit": limit}
        )
        return data.get("items", [])
    except Exception:
        return []


async def _get_top_tracks(access_token: str, time_range: str, limit: int = 20) -> list[dict]:
    try:
        data = await _spotify_get(
            "/me/top/tracks", access_token, {"time_range": time_range, "limit": limit}
        )
        return data.get("items", [])
    except Exception:
        return []


async def _get_playlists(access_token: str, limit: int = 20) -> list[dict]:
    try:
        data = await _spotify_get("/me/playlists", access_token, {"limit": limit})
        return data.get("items", [])
    except Exception:
        return []


def _time_range_label(time_range: str) -> str:
    return {
        "short_term": "last 4 weeks",
        "medium_term": "last 6 months",
        "long_term": "all-time",
    }.get(time_range, time_range)


def _build_raw_content(
    display_name: str,
    time_range: str,
    top_artists: list[dict],
    top_tracks: list[dict],
    playlists: list[dict],
) -> str:
    """Assemble the raw_content blob."""
    parts = [f"[Spotify profile] {display_name}"]

    label = _time_range_label(time_range)

    if top_artists:
        parts.append(f"\n[Top artists — {label}]")
        for a in top_artists:
            name = a.get("name", "")
            genres = ", ".join(a.get("genres", [])[:3])
            parts.append(f"  {name}" + (f" — genres: {genres}" if genres else ""))

    if top_tracks:
        parts.append(f"\n[Top tracks — {label}]")
        for t in top_tracks:
            name = t.get("name", "")
            artists = t.get("artists", [])
            artist = artists[0].get("name", "") if artists else ""
            album = t.get("album", {}).get("name", "") if isinstance(t.get("album"), dict) else ""
            parts.append(f"  {name} by {artist}" + (f" (from {album})" if album else "") if artist else f"  {name}")

    if playlists:
        parts.append("\n[Playlists]")
        for pl in playlists:
            name = pl.get("name", "")
            track_count = pl.get("tracks", {}).get("total", 0) if isinstance(pl.get("tracks"), dict) else 0
            owner = pl.get("owner", {}).get("display_name", "") if isinstance(pl.get("owner"), dict) else ""
            suffix = f" — {track_count} tracks"
            if owner and owner != display_name:
                suffix += f" (by {owner})"
            parts.append(f"  {name}{suffix}")

    return "\n".join(parts)


async def scrape_spotify(profile_id: int, entity_type: str = "People", time_range: str = "medium_term") -> Tuple[str, list[dict]]:
    """
    Full Spotify scrape pipeline for a profile's linked account.
    Fetches top artists/tracks for `time_range` plus playlists.
    Returns (raw_content, posts) where posts is a metadata dict.

    Not linked: returns ("", [...]) without raising, so the caller's
    dispatch (which only appends non-empty text) skips it cleanly.
    """
    if time_range not in VALID_TIME_RANGES:
        time_range = "medium_term"

    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p or not p.spotify_access_token:
            return "", [{"source": "spotify", "linked": False}]
        entity_name = p.name
        prior_spotify_user_id = p.spotify_user_id or ""
        prior_display_name = p.spotify_display_name or ""
    finally:
        db.close()

    cached = get_spotify_cache(entity_name, entity_type)
    if cached:
        return cached, [{"source": "spotify", "cached": True}]

    access_token = await ensure_fresh_token(profile_id)
    if not access_token:
        return "", [{"source": "spotify", "linked": False}]

    me = await _get_me(access_token)
    spotify_user_id = me.get("id", prior_spotify_user_id)
    display_name = me.get("display_name") or prior_display_name or entity_name

    top_artists = await _get_top_artists(access_token, time_range)
    top_tracks = await _get_top_tracks(access_token, time_range)
    playlists = await _get_playlists(access_token)

    raw = _build_raw_content(display_name, time_range, top_artists, top_tracks, playlists)

    if len(raw) > settings.content_max_chars:
        raw = raw[: settings.content_max_chars]

    save_spotify_cache(entity_name, entity_type, raw, _spotify_source_url(spotify_user_id))

    return raw, [{"source": "spotify", "cached": False}]


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from scraped Spotify content via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given music-listening facts about a person named "{name}", generate exactly 50 trivia questions about their music taste.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions should be about favorite artists, tracks, and playlists
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
        print(f"Error generating Spotify questions: {e}")
        return []
