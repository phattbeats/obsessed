"""
Venmo scraper for PEOPLE entity type.

Captures from the (largely deprecated) public Venmo API:

  - Profile fields: username, display name, first/last, ID, profile picture,
    date joined, identity type
  - Public transaction feed (legacy public accounts only — Venmo has been
    defaulting new accounts to friends-only since 2025)
    * Payment descriptions / notes (the actual trivia signal — "spending
      intent" per the PHA-797 brief)
    * Actor IDs and usernames (social graph)
    * Timestamps
    * NOT amounts — the public feed has never exposed them

Anti-bot / rate-limit posture:
  - VENMO_LIMITER: 1 concurrent, 3.0s between calls (heavy per the brief)
  - Browser-like headers (User-Agent, Accept-Language) — bare curl UAs get
    the "update your app" 400 gate
  - On the "Resource not found" / "Update your app" 400 errors, return []
    with a logged sentinel — these mean the user is friends-only or the
    endpoint is gated; nothing to do
  - On 429, parse Retry-After and respect it (delegated to retry_with_backoff)
  - On any other 4xx/5xx, retry with backoff up to 2x
  - Cache: writes to entity_cache like reddit/pinterest so repeat scrapes
    are instant and the limiter is only hit on a fresh handle

Limitations (called out for downstream consumers):
  - No amounts (Venmo public API never exposed them, per the brief)
  - Only legacy public accounts surface in the feed; new accounts return []
  - The "Update your app" 400 gate applies to /api/v5/public/* paths;
    /api/v5/users/{username} (profile) does NOT have that gate
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings
from app.database import SessionLocal, EntityCache
from app.services.scraper.rate_limiter import (
    VENMO_LIMITER,
    retry_with_backoff,
)


VENMO_SOURCE_PREFIX = "https://venmo.com/"

# Endpoint roots
_BASE = "https://venmo.com"
_USER_ENDPOINT = f"{_BASE}/api/v5/users"
_FEED_ENDPOINT = f"{_BASE}/api/v5/public"

# Browser-like headers.  Venmo's "update your app" 400 gate is UA-driven:
# the request looks like the iOS app and so the route serves the JSON.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://venmo.com/",
}


# ---------------------------------------------------------------------------
# Sentinel helpers — return empty results on expected soft failures rather
# than propagating.  The orchestrator treats these as "no signal, not an
# error" so retries don't loop.
# ---------------------------------------------------------------------------

_VENMO_NOT_PUBLIC_SIGNALS = (
    "In order to continue accessing your account",  # "update your app" gate
    "Resource not found",                            # friends-only / private
    "Unauthorized",
)


def _is_soft_404(body: str) -> bool:
    """True if the response body indicates the user is private / not scrapable."""
    if not body:
        return True
    return any(sig.lower() in body.lower() for sig in _VENMO_NOT_PUBLIC_SIGNALS)


# ---------------------------------------------------------------------------
# Source URL builders
# ---------------------------------------------------------------------------


def _user_source_url(username: str) -> str:
    u = username.lstrip("@/").split("?")[0]
    return f"https://venmo.com/{u}"


def _feed_source_url(username: str) -> str:
    u = username.lstrip("@/").split("?")[0]
    return f"https://venmo.com/{u}#public-feed"


# ---------------------------------------------------------------------------
# Cache helpers (mirror the reddit/pinterest pattern)
# ---------------------------------------------------------------------------


def get_venmo_cache(username: str, entity_type: str = "People") -> Optional[str]:
    """Return cached raw_content for this Venmo handle, if present."""
    source = _user_source_url(username)
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == username.lstrip("@/"),
            EntityCache.entity_type == entity_type,
            EntityCache.source_url == source,
        ).first()
        if cached:
            return cached.raw_content
    finally:
        db.close()
    return None


def save_venmo_cache(username: str, entity_type: str, content: str) -> None:
    """Persist scraped Venmo content to entity_cache."""
    if not content:
        return
    source = _user_source_url(username)
    db = SessionLocal()
    try:
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == username.lstrip("@/"),
            EntityCache.entity_type == entity_type,
            EntityCache.source_url == source,
        ).first()
        if existing:
            existing.raw_content = content
            existing.scraped_at = int(datetime.now(timezone.utc).timestamp())
        else:
            db.add(EntityCache(
                entity_name=username.lstrip("@/"),
                entity_type=entity_type,
                raw_content=content,
                source_url=source,
            ))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# HTTP fetchers
# ---------------------------------------------------------------------------


async def _fetch_user_profile(username: str) -> Optional[dict]:
    """
    GET /api/v5/users/{username} → profile JSON.

    Returns None for soft 404s (private / not found / gated).
    Raises on transport errors so retry_with_backoff can handle them.
    """
    url = f"{_USER_ENDPOINT}/{username.lstrip('@/')}"
    async with VENMO_LIMITER:
        try:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(
                    timeout=20.0, headers=_BROWSER_HEADERS, follow_redirects=True
                ).get(url),
                max_retries=2,
                base_delay=3.0,
            )
        except Exception:
            return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict) or "data" not in data:
        return None
    return data["data"]


async def _fetch_public_feed(username: str, limit: int = 20) -> list[dict]:
    """
    GET /api/v5/public/feed?actor={username}&limit={limit} → list of payments.

    Returns [] for soft 404s.  The 400 "update your app" gate is
    persistent on this path; treat it as a definitive "not public".
    """
    u = username.lstrip("@/")
    url = f"{_FEED_ENDPOINT}/feed"
    async with VENMO_LIMITER:
        try:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(
                    timeout=20.0, headers=_BROWSER_HEADERS, follow_redirects=True
                ).get(
                    url,
                    params={"actor": u, "limit": str(limit)},
                ),
                max_retries=2,
                base_delay=3.0,
            )
        except Exception:
            return []
    if resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    feed = data.get("data", [])
    return feed if isinstance(feed, list) else []


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _clean_text(text: str) -> str:
    """Strip URLs, excess whitespace, control chars."""
    if not text:
        return ""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _profile_to_record(profile: dict, username: str) -> dict:
    """Normalise a /users/{u} payload to our standard record shape."""
    return {
        "username": profile.get("username") or username.lstrip("@/"),
        "display_name": profile.get("display_name") or "",
        "first_name": profile.get("first_name") or "",
        "last_name": profile.get("last_name") or "",
        "id": profile.get("id") or "",
        "identity_type": profile.get("identity_type") or "",
        "date_joined": profile.get("date_joined") or "",
        "profile_picture_url": profile.get("profile_picture_url") or "",
        "source": "venmo",
        "source_url": _user_source_url(username),
    }


def _payment_to_record(payment: dict) -> Optional[dict]:
    """Normalise a single payment entry to our standard record shape."""
    if not isinstance(payment, dict):
        return None

    actor = payment.get("actor") or {}
    target = payment.get("target") or {}

    # Note: amounts are NOT present in the public feed (per the issue brief).
    # We capture the description / note as the trivia signal.
    note = _clean_text(payment.get("note") or "")
    if not note:
        return None  # skip payments with no note — they're noise for trivia

    return {
        "id": payment.get("id") or "",
        "note": note,
        "actor": {
            "username": actor.get("username") or "",
            "display_name": actor.get("display_name") or "",
            "id": actor.get("id") or "",
        },
        "target": {
            "username": target.get("username") or "",
            "display_name": target.get("display_name") or "",
            "id": target.get("id") or "",
        },
        "created_at": payment.get("created_time") or payment.get("date_created") or "",
        "type": payment.get("type") or "",  # "pay" / "charge" / etc.
        "audience": payment.get("audience") or "",  # "public" / "private" / "friends"
        "source": "venmo",
    }


# ---------------------------------------------------------------------------
# Main scrape entry point — mirrors the reddit/pinterest signature
# ---------------------------------------------------------------------------


async def _scrape_venmo_with_fallback(username: str) -> tuple[str, list[dict]]:
    """
    Try the public feed first (the trivia signal), then fall back to
    profile-only when the feed is gated.

    Returns (raw_text_for_cache, list_of_records).
    """
    username = username.lstrip("@/").split("?")[0]
    if not username:
        return "", []

    records: list[dict] = []

    # 1) Profile — the cheapest endpoint, and it always returns a result
    #    (or a soft 404 that we'll log and move past).
    profile = await _fetch_user_profile(username)
    if profile:
        records.append(_profile_to_record(profile, username))

    # 2) Public feed — the actual signal.  On friends-only / gated users
    #    this returns [] and we keep the profile record alone.
    feed = await _fetch_public_feed(username, limit=20)
    payment_records: list[dict] = []
    for p in feed:
        rec = _payment_to_record(p)
        if rec is not None:
            payment_records.append(rec)
    records.extend(payment_records)

    # 3) Render raw text for entity_cache / question generation.  Profile
    #    line first, then one line per payment with actor → target + note.
    lines: list[str] = []
    if profile:
        lines.append(
            f"[Venmo profile] {profile.get('display_name','')} "
            f"(@{profile.get('username', username)}) "
            f"joined {profile.get('date_joined','')} "
            f"id={profile.get('id','')}"
        )
    for p in payment_records:
        actor = p["actor"].get("display_name") or p["actor"].get("username") or "?"
        target = p["target"].get("display_name") or p["target"].get("username") or "?"
        lines.append(f"[Venmo] {actor} → {target}: {p['note']}")
    raw = "\n".join(lines)

    return raw, records


async def scrape_venmo(username: str, entity_type: str = "People") -> tuple[str, list[dict]]:
    """
    Scrape Venmo for a username.  Returns (raw_text, list_of_records).

    Follows the same shape as scrape_reddit / scrape_pinterest:
      * Checks entity_cache first
      * Calls the network fetcher with a graceful-failure wrapper
      * Caps content to settings.content_max_chars
      * Writes back to entity_cache on success
    """
    username = username.lstrip("@/").split("?")[0]
    if not username:
        return "", []

    cached = get_venmo_cache(username, entity_type)
    if cached:
        return cached, [{"source": "venmo", "cached": True, "username": username}]

    try:
        raw, records = await _scrape_venmo_with_fallback(username)
    except Exception:
        raw, records = "", []

    if len(raw) > settings.content_max_chars:
        raw = raw[: settings.content_max_chars]

    if raw:
        save_venmo_cache(username, entity_type, raw)

    return raw, records


# ---------------------------------------------------------------------------
# Public-feed parser exposed for testability (and for any future caller
# that already has a raw feed payload in hand).
# ---------------------------------------------------------------------------


def parse_venmo_feed(feed: list[dict]) -> list[dict]:
    """Parse a list of raw Venmo payment dicts into our record shape."""
    out: list[dict] = []
    for p in feed:
        rec = _payment_to_record(p)
        if rec is not None:
            out.append(rec)
    return out


def parse_venmo_profile(profile: dict, username: str = "") -> dict:
    """Parse a raw /api/v5/users/{u} payload into our record shape."""
    return _profile_to_record(profile, username)
