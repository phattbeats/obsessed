"""
Regression guard for PHA-498: scraper cache helpers must use real EntityCache
columns (raw_content, source_url) — never fabricated ones (content, source,
created_at, updated_at). Catches schema drift at PR time without needing the
real network endpoints up.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.database import EntityCache, SessionLocal
from app.services.scraper import instagram, reddit, twitter
from app.services.scraper.reddit import (
    get_reddit_cache,
    save_reddit_cache,
    scrape_reddit,
)
from app.services.scraper.instagram import (
    get_instagram_cache,
    save_instagram_cache,
    scrape_instagram,
)
from app.services.scraper.twitter import (
    get_twitter_cache,
    save_twitter_cache,
    scrape_twitter,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Wipe entity_cache before/after each test for isolation."""
    db = SessionLocal()
    try:
        db.query(EntityCache).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(EntityCache).delete()
        db.commit()
    finally:
        db.close()


def _row_count() -> int:
    db = SessionLocal()
    try:
        return db.query(EntityCache).count()
    finally:
        db.close()


def test_reddit_cache_roundtrip():
    save_reddit_cache("alice", "People", "reddit content for alice")
    assert get_reddit_cache("alice", "People") == "reddit content for alice"

    db = SessionLocal()
    try:
        row = db.query(EntityCache).filter_by(entity_name="alice").one()
        assert row.raw_content == "reddit content for alice"
        assert row.source_url.startswith("https://old.reddit.com/")
        assert row.scraped_at  # default-populated
    finally:
        db.close()

    # update path: same handle re-saves into the same row
    save_reddit_cache("alice", "People", "newer content")
    assert get_reddit_cache("alice", "People") == "newer content"
    assert _row_count() == 1


def test_instagram_cache_roundtrip():
    save_instagram_cache("bob", "People", "ig content for bob")
    assert get_instagram_cache("bob", "People") == "ig content for bob"
    db = SessionLocal()
    try:
        row = db.query(EntityCache).filter_by(entity_name="bob").one()
        assert row.raw_content == "ig content for bob"
        assert row.source_url.startswith("https://www.instagram.com/")
    finally:
        db.close()


def test_twitter_cache_roundtrip():
    save_twitter_cache("charlie", "People", "twitter content for charlie")
    assert get_twitter_cache("charlie", "People") == "twitter content for charlie"
    db = SessionLocal()
    try:
        row = db.query(EntityCache).filter_by(entity_name="charlie").one()
        assert row.raw_content == "twitter content for charlie"
        assert row.source_url.startswith("https://x.com/")
    finally:
        db.close()

    # update path: same handle re-saves into the same row
    save_twitter_cache("charlie", "People", "newer twitter content")
    assert get_twitter_cache("charlie", "People") == "newer twitter content"
    assert _row_count() == 1


def test_per_source_cache_isolation():
    """twitter, reddit, instagram entries coexist without collision."""
    save_reddit_cache("alice", "People", "reddit alice")
    save_instagram_cache("alice", "People", "ig alice")
    save_twitter_cache("alice", "People", "twitter alice")
    assert _row_count() == 3
    assert get_reddit_cache("alice", "People") == "reddit alice"
    assert get_instagram_cache("alice", "People") == "ig alice"
    assert get_twitter_cache("alice", "People") == "twitter alice"


@pytest.mark.asyncio
async def test_scrape_reddit_end_to_end_with_mocked_network():
    """
    Exercises scrape_reddit() with HTTP fully mocked. Catches schema drift
    on both the read (cache miss) and write (cache populate) paths.
    """
    async def _fake_scrape(handle: str) -> str:
        return f"[Reddit r/test] post about {handle}"

    with patch.object(reddit, "scrape_reddit_with_fallback", _fake_scrape):
        # First call: cache miss → scrapes → writes
        text, meta = await scrape_reddit("erin", "People")
        assert "post about erin" in text
        assert meta == [{"source": "reddit", "cached": False}]

        # Second call: cache hit → no schema errors
        text2, meta2 = await scrape_reddit("erin", "People")
        assert text2 == text
        assert meta2 == [{"source": "reddit", "cached": True}]


@pytest.mark.asyncio
async def test_scrape_instagram_end_to_end_with_mocked_network():
    async def _fake(handle: str, entity_type: str = "People"):
        return (f"[Instagram profile: @{handle}]", {"username": handle})

    with patch.object(instagram, "scrape_instagram_with_fallback", _fake):
        text, profile = await scrape_instagram("frank", "People")
        assert "frank" in text
        assert profile["username"] == "frank"

        text2, profile2 = await scrape_instagram("frank", "People")
        assert text2 == text
        assert profile2 == {"source": "instagram", "cached": True}


@pytest.mark.asyncio
async def test_scrape_twitter_end_to_end_with_mocked_network():
    """
    Exercises scrape_twitter() with subprocess fully mocked.
    Catches cache read/write errors without needing real Twitter cookies.
    """
    import asyncio

    # Fake subprocess results keyed by command name
    FAKE_OUTPUTS = {
        "user": b'{"ok":true,"schema_version":"1","data":[{"id":"1","name":"Test User","screenName":"testuser","description":"A test account"}]}',
        "user-posts": b'{"ok":true,"schema_version":"1","data":[{"id":"1","text":"Test tweet from @testuser"},{"id":"2","text":"Second tweet"}]}',
    }

    @staticmethod
    async def _fake_wait_for(coro, timeout=None):
        return await coro

    fake_proc_id = [0]
    async def _fake_create_subprocess_exec(*args, **kwargs):
        proc_id = fake_proc_id[0]
        fake_proc_id[0] += 1
        # args: (TWITTER_CLI_PATH, "user" or "user-posts", handle, "--json", env=..., stdout=..., stderr=...)
        cmd_name = "user-posts" if len(args) > 1 and "user-posts" in str(args[1]) else "user"
        stdout_data = FAKE_OUTPUTS.get(cmd_name, b'{"ok":false}')

        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(stdout_data, b""))
        return proc

    async def _fake_wait_for_coro(coro, timeout=None):
        return await coro

    with patch("asyncio.create_subprocess_exec", _fake_create_subprocess_exec), \
         patch("asyncio.wait_for", _fake_wait_for_coro):
        # First call: cache miss → scrapes → writes
        text, meta = await scrape_twitter("testuser", "People")
        assert "testuser" in text.lower()
        assert meta == [{"source": "twitter", "cached": False}]

        # Second call: cache hit
        text2, meta2 = await scrape_twitter("testuser", "People")
        assert text2 == text
        assert meta2 == [{"source": "twitter", "cached": True}]