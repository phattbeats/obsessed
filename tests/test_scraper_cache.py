"""
Regression guard for PHA-498: scraper cache helpers must use real EntityCache
columns (raw_content, source_url) — never fabricated ones (content, source,
created_at, updated_at). Catches schema drift at PR time without needing the
real network endpoints up.
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import EntityCache, SessionLocal
from app.services.scraper import instagram, reddit, twitter, facebook
from app.services.scraper.facebook import (
    get_facebook_cache,
    save_facebook_cache,
    scrape_facebook,
)
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
from app.services.scraper.twitter_scraper import (
    get_twitter_cache,
    save_twitter_cache,
    scrape_twitter,
)


def _mock_response(data: dict):
    """
    Build a mock httpx.Response. Response.json() and Response.raise_for_status()
    are sync on real httpx — use MagicMock so calling them returns the value
    directly instead of an unawaited coroutine.
    """
    res = MagicMock()
    res.raise_for_status = MagicMock()
    res.json = MagicMock(return_value=data)
    return res


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

    with patch.dict(os.environ, {"TWITTER_AUTH_TOKEN": "fake_token_for_test", "TWITTER_CT0": "fake_ct0_for_test"}), \
         patch("asyncio.create_subprocess_exec", _fake_create_subprocess_exec), \
         patch("asyncio.wait_for", _fake_wait_for_coro):
        # First call: cache miss → scrapes → writes
        text, meta = await scrape_twitter("testuser", "People")
        assert "testuser" in text.lower()
        assert meta == [{"source": "twitter", "cached": False}]

        # Second call: cache hit
        text2, meta2 = await scrape_twitter("testuser", "People")
        assert text2 == text
        assert meta2 == [{"source": "twitter", "cached": True}]

@pytest.mark.asyncio
async def test_scrape_facebook_end_to_end_with_mocked_network():
    """
    Exercises scrape_facebook() with crawl4ai mocked.
    Covers: happy path (page name + followers), login wall, crawl4ai error.
    """
    from unittest.mock import AsyncMock, patch

    MARKDOWN_HAPPY = """# Nintendo of America

## NintendoAmerica

**Verified** ✓
**5,600,000** Followers

Category: Video Game Company

Nintendo of America, headquartered in Redmond, WA, is a leader in the video game industry.

Latest Post: January 15, 2024 at 3:30 PM
Great news — check out our latest direct!
125,000 likes
4,200 comments
(https://www.facebook.com/NintendoAmerica/posts/123)
"""

    async def fake_crawl4ai_post(url, **kwargs):
        # Production posts to CRAWL4AI_URL with the FB page URL inside the JSON body.
        # Inspect the json kwarg to detect which FB page is being scraped.
        body = kwargs.get("json") or {}
        page_urls = body.get("urls") or []
        page_url = page_urls[0] if page_urls else url
        if "about" in page_url:
            data = {"results": [{"success": True, "markdown": MARKDOWN_HAPPY.replace("Latest Post", "About Section")}]}
        elif "login" in page_url.lower():
            data = {"results": [{"success": True, "markdown": "Mobile number or email\nCreate new account\nlog in to facebook"}]}
        elif "notfound" in page_url:
            data = {"results": [{"success": True, "markdown": "# Page not found\nSorry, this content isn't available"}]}
        else:
            data = {"results": [{"success": True, "markdown": MARKDOWN_HAPPY}]}
        return _mock_response(data)

    async def fake_wait_for(coro, timeout=None):
        return await coro

    with patch("httpx.AsyncClient") as MockClient, \
         patch("asyncio.wait_for", fake_wait_for):
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = fake_crawl4ai_post
        MockClient.return_value = mock_inst

        # Clear cache
        db = SessionLocal()
        try:
            db.query(EntityCache).filter(
                EntityCache.entity_name == "NintendoAmerica",
                EntityCache.source_url.like("https://www.facebook.com/%"),
            ).delete()
            db.commit()
        finally:
            db.close()

        # Happy path
        text, meta = await scrape_facebook("NintendoAmerica", "People")
        assert "Nintendo" in text
        assert meta.get("source") == "facebook"
        assert meta.get("cached") is False

        # Cache hit
        text2, meta2 = await scrape_facebook("NintendoAmerica", "People")
        assert text2 == text
        assert meta2 == {"source": "facebook", "cached": True}

    # Login wall sentinel
    with patch("httpx.AsyncClient") as MockClient, \
         patch("asyncio.wait_for", fake_wait_for):
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = fake_crawl4ai_post
        MockClient.return_value = mock_inst

        text3, meta3 = await scrape_facebook("login", "People")
        assert text3 == "[Facebook: login wall]"
        assert meta3 == {}

    # crawl4ai error sentinel
    async def fake_error_post(url, **kwargs):
        raise Exception("connection refused")

    with patch("httpx.AsyncClient") as MockClient, \
         patch("asyncio.wait_for", fake_wait_for):
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = fake_error_post
        MockClient.return_value = mock_inst

        text4, meta4 = await scrape_facebook("error_test", "People")
        # crawl4ai errors are surfaced verbatim (with "[crawl4ai error: ...]" prefix)
        # or wrapped as Facebook sentinels; either is acceptable graceful failure.
        assert text4.startswith("[crawl4ai error") or text4.startswith("[Facebook:")
        assert meta4 == {}


def test_write_cached_basic():
    """Regression for PHA-788: write_cached must not raise NameError on timezone."""
    from app.services.entity_cache import write_cached, get_cached

    ok = write_cached("test_entity", "Places", "some scraped content", "https://example.com")
    assert ok is True

    result = get_cached("test_entity", "Places")
    assert result is not None
    content, meta = result
    assert content == "some scraped content"
    assert meta["source_url"] == "https://example.com"
    assert meta["scraped_at"] > 0


def test_crawl4ai_empty_token_no_bearer_header():
    """Regression for PHA-790: must not send 'Bearer ' when token is empty."""
    from unittest.mock import patch
    from app.services.scraper.crawl4ai import _crawl4ai_headers, crawl4ai_scrape

    # Empty token → no Authorization header
    with patch("app.services.scraper.crawl4ai.settings") as mock_settings:
        mock_settings.crawl4ai_token = ""
        headers = _crawl4ai_headers()
        assert headers == {}

    # Non-empty token → Bearer header
    with patch("app.services.scraper.crawl4ai.settings") as mock_settings:
        mock_settings.crawl4ai_token = "my-token"
        headers = _crawl4ai_headers()
        assert headers == {"Authorization": "Bearer my-token"}
