"""
Regression guard for PHA-498: scraper cache helpers must use real EntityCache
columns (raw_content, source_url) — never fabricated ones (content, source,
created_at, updated_at). Catches schema drift at PR time without needing the
real network endpoints up.
"""
from unittest.mock import patch

import pytest

from app.database import EntityCache, SessionLocal
from app.services.scraper import instagram, reddit, threads
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
from app.services.scraper.threads import (
    get_threads_cache,
    save_threads_cache,
    scrape_threads,
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
        assert row.source_url.startswith("https://www.reddit.com/")
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


def test_threads_cache_roundtrip():
    save_threads_cache("carol", "People", "threads content for carol")
    assert get_threads_cache("carol", "People") == "threads content for carol"
    db = SessionLocal()
    try:
        row = db.query(EntityCache).filter_by(entity_name="carol").one()
        assert row.raw_content == "threads content for carol"
        assert row.source_url.startswith("https://threads.net/")
    finally:
        db.close()


def test_per_source_cache_isolation():
    """Same entity_name+entity_type cached from each source must not collide."""
    save_reddit_cache("dave", "People", "reddit-dave")
    save_instagram_cache("dave", "People", "ig-dave")
    save_threads_cache("dave", "People", "threads-dave")

    assert get_reddit_cache("dave", "People") == "reddit-dave"
    assert get_instagram_cache("dave", "People") == "ig-dave"
    assert get_threads_cache("dave", "People") == "threads-dave"
    assert _row_count() == 3


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
async def test_scrape_threads_end_to_end_with_mocked_network():
    async def _fake(handle: str, entity_type: str = "People"):
        return (f"[Threads profile: @{handle}]", {"username": handle})

    with patch.object(threads, "scrape_threads_with_fallback", _fake):
        text, profile = await scrape_threads("gina", "People")
        assert "gina" in text
        assert profile["username"] == "gina"

        text2, profile2 = await scrape_threads("gina", "People")
        assert text2 == text
        assert profile2 == {"source": "threads", "cached": True}
