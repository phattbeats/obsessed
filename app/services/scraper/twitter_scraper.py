"""
Twitter scraper for PEOPLE entity type.

Uses the twitter-cli.sh subprocess to fetch user profile + recent tweets.
Auth via TWITTER_AUTH_TOKEN + TWITTER_CT0 env vars (set in .env).

Cache-aware via entity_cache (checks/writes before/after scrape).
"""
import asyncio, json, logging, os, subprocess
from typing import Optional
from app.config import settings
from app.database import SessionLocal, EntityCache

logger = logging.getLogger(__name__)

TWITTER_SOURCE_PREFIX = "https://x.com/"

# Env var names for twitter-cli cookie auth
TWITTER_AUTH_TOKEN_ENV = "TWITTER_AUTH_TOKEN"
TWITTER_CT0_ENV = "TWITTER_CT0"

# Path to the twitter-cli wrapper (installed on host, accessible from container)
TWITTER_CLI_PATH = os.environ.get("TWITTER_CLI_PATH", "/root/.openclaw/utilities/twitter-cli.sh")


def get_twitter_cache(entity_name: str, entity_type: str = "People") -> Optional[str]:
    """Check entity_cache for existing Twitter content."""
    db = SessionLocal()
    try:
        cached = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{TWITTER_SOURCE_PREFIX}%"),
        ).first()
        return cached.raw_content if cached else None
    finally:
        db.close()


def save_twitter_cache(entity_name: str, entity_type: str, content: str):
    """Save scraped Twitter content to entity_cache."""
    db = SessionLocal()
    try:
        source_url = f"{TWITTER_SOURCE_PREFIX}{entity_name.lstrip('@')}"
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
            EntityCache.source_url.like(f"{TWITTER_SOURCE_PREFIX}%"),
        ).first()
        if existing:
            existing.raw_content = content
            from datetime import datetime
            existing.scraped_at = int(datetime.now(timezone.utc).timestamp())
        else:
            db.add(EntityCache(
                entity_name=entity_name,
                entity_type=entity_type,
                raw_content=content,
                source_url=source_url,
            ))
        db.commit()
    finally:
        db.close()


def _build_twitter_env() -> dict:
    """Build env dict for twitter-cli subprocess."""
    env = dict(os.environ)
    for var in (TWITTER_AUTH_TOKEN_ENV, TWITTER_CT0_ENV):
        val = os.environ.get(var)
        if val:
            env[var] = val
        else:
            env.pop(var, None)
    return env


def _check_cookies() -> bool:
    """Return True if TWITTER_AUTH_TOKEN is set (even if ct0 is missing)."""
    return bool(os.environ.get(TWITTER_AUTH_TOKEN_ENV))


async def scrape_twitter(handle: str, entity_type: str = "People") -> tuple[str, list[dict]]:
    """
    Scrape a Twitter/X user profile + recent tweets via twitter-cli subprocess.
    Returns (raw_text, posts).
    Cache-aware: checks entity_cache first, writes on success.
    Graceful failure if cookies missing — returns sentinel text, not exception.
    """
    handle = handle.lstrip("@/")
    if not handle:
        return "[Twitter: empty handle]", []

    # Check cache first
    cached = get_twitter_cache(handle, entity_type)
    if cached:
        return cached, [{"source": "twitter", "cached": True}]

    # Check cookies
    if not _check_cookies():
        logger.warning("Twitter scrape skipped: TWITTER_AUTH_TOKEN not set")
        return "[Twitter: missing cookies — set TWITTER_AUTH_TOKEN env var]", []

    raw_parts = []

    # Fetch user profile
    try:
        env = _build_twitter_env()
        result = await asyncio.create_subprocess_exec(
            TWITTER_CLI_PATH, "user", handle, "--json",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=30.0)
        if result.returncode == 0:
            profile_data = json.loads(stdout)
            if profile_data.get("ok"):
                screen_name = profile_data.get("data", [{}])[0].get("screenName", handle)
                raw_parts.append(f"[@{screen_name} profile]")

        # Fetch user posts
        result2 = await asyncio.create_subprocess_exec(
            TWITTER_CLI_PATH, "user-posts", handle, "--max", "20", "--json",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout2, stderr2 = await asyncio.wait_for(result2.communicate(), timeout=60.0)
        if result2.returncode == 0:
            posts_data = json.loads(stdout2)
            if posts_data.get("ok") and posts_data.get("data"):
                tweets = []
                for tweet in posts_data["data"]:
                    text = tweet.get("text", "")
                    if text:
                        tweets.append(text)
                raw_parts.append(f"[Recent tweets from @{handle}]\n" + "\n".join(f"- {t}" for t in tweets))

    except asyncio.TimeoutError:
        logger.warning("Twitter scrape timed out for @%s", handle)
        raw_parts.append(f"[Twitter: timeout fetching @{handle}]")
    except Exception as e:
        logger.warning("Twitter scrape failed for @%s: %s", handle, e)
        raw_parts.append(f"[Twitter: scrape failed — {e}]")

    raw = "\n\n".join(raw_parts) if raw_parts else f"[Twitter: no data retrieved for @{handle}]"

    # Cap at content_max_chars
    raw = raw[: settings.content_max_chars]

    # Save to cache
    if raw and not raw.startswith("[Twitter: missing cookies]") and not raw.startswith("[Twitter: scrape failed"):
        save_twitter_cache(handle, entity_type, raw)

    return raw, [{"source": "twitter", "cached": False}]