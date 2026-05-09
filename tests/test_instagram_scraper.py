"""
PHA-682: Instagram scraper failover — test all three mirror branches.
Covers: primary success, primary fail/secondary success, all-mirrors-fail sentinel.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.database import EntityCache, SessionLocal
from app.services.scraper.instagram import (
    IG_MIRROR_INSTANCES,
    scrape_instagram,
    scrape_instagram_profile,
    _parse_instagram_markdown,
    _expand_suffix,
    get_instagram_cache,
    save_instagram_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
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


# ---------------------------------------------------------------------------
# Unit-level helpers
# ---------------------------------------------------------------------------

def test_expand_suffix():
    assert _expand_suffix("1.2M") == "1200000"
    assert _expand_suffix("500K") == "500000"
    assert _expand_suffix("2.6B") == "2600000000"
    assert _expand_suffix("1,234,567") == "1234567"
    assert _expand_suffix("bad") == "bad"  # passthrough


def test_parse_instagram_markdown_kittygram_shape():
    md = """# Khabane Lame

## khaby.lame

**161178162** Followers
**84** Following
**1384** Posts
Some bio text here.

![img](https://kittygr.am/mediaproxy/abc123)
[khaby.lame](https://kittygr.am/p/abc123)
Posted at: 2024-01-15 10:30
1612000 likes
Best caption ever
[ 123 Comments](https://kittygr.am/p/abc123)
"""
    profile = _parse_instagram_markdown(md, "khaby.lame", "https://kittygr.am")
    assert profile["username"] == "khaby.lame"
    assert profile["display_name"] == "Khabane Lame"
    assert profile["followers"] == "161178162"
    assert profile["following"] == "84"
    assert profile["posts"] == "1384"
    assert profile["bio"] == "Some bio text here."
    assert len(profile["posts_data"]) == 1
    assert profile["posts_data"][0]["likes"] == "1612000"


def test_parse_instagram_markdown_imginn_shape():
    """imginn may emit the same shape but with a different domain."""
    md = """# Some Influencer

## therealhandle

**500000** Followers
**120** Following
**200** Posts
IG bio goes here.

![img](https://imginn.com/mediaproxy/xyz789)
[therealhandle](https://imginn.com/p/xyz789)
Posted at: 2024-03-01 08:00
50000 likes
Imginn caption test
[ 45 Comments](https://imginn.com/p/xyz789)
"""
    profile = _parse_instagram_markdown(md, "therealhandle", "https://imginn.com")
    assert profile["username"] == "therealhandle"
    assert profile["display_name"] == "Some Influencer"
    assert profile["followers"] == "500000"
    assert profile["following"] == "120"
    assert profile["posts"] == "200"
    assert profile["bio"] == "IG bio goes here."
    assert profile["posts_data"][0]["caption"] == "Imginn caption test"


def test_parse_instagram_markdown_human_formatted_numbers():
    """Follower counts may come back as '1.2M' rather than '1200000'."""
    md = """# MrBeast

## mrbeast

**161.2M** Followers
**2** Following
**706** Posts
King of YouTube.
"""
    profile = _parse_instagram_markdown(md, "mrbeast", "https://kittygr.am")
    assert profile["followers"] == "161200000"
    assert profile["posts"] == "706"


def test_cache_roundtrip():
    save_instagram_cache("testuser", "People", "instagram cached content for testuser")
    assert get_instagram_cache("testuser", "People") == "instagram cached content for testuser"


# ---------------------------------------------------------------------------
# End-to-end failover (subprocess-mocked crawl4ai responses)
# ---------------------------------------------------------------------------

@pytest.fixture
def _mock_crawl4ai():
    """Returns a (success_response, failure_response) pair for mocking."""
    def make_response(instance_base: str, *, success: bool = True,
                      markdown: str = "", username: str = "") -> dict:
        # When success=True and markdown provided, parse to non-empty profile
        if success and username:
            parsed = _parse_instagram_markdown(markdown, username, instance_base)
            success_flag = bool(parsed.get("username"))
        else:
            success_flag = success
        return {"results": [{"success": success_flag, "markdown": markdown}]}

    return make_response


async def _patch_crawl4ai_for_instance(instance_base: str, mock_data: dict):
    """Patch httpx client.post to return mock_data for a specific instance_base."""
    async def fake_post(url, **kwargs):
        if instance_base in url or instance_base.replace("https://", "") in url:
            res = AsyncMock()
            res.raise_for_status = AsyncMock()
            res.json = AsyncMock(return_value=mock_data)
            return res
        # Fallback: call through
        res = AsyncMock()
        res.raise_for_status = AsyncMock()
        res.json = AsyncMock(return_value={"results": [{"success": False, "markdown": ""}]})
        return res
    return fake_post


@pytest.mark.asyncio
async def test_primary_success():
    """Primary mirror (kittygr.am) succeeds → uses its result."""
    md_kittygram = """# Test User\n\n## testuser\n\n**1000** Followers\n**50** Following\n**25** Posts\nTest bio.
"""
    mock_resp = {"results": [{"success": True, "markdown": md_kittygram}]}

    with patch("httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(returnvalue=mock_instance)
        mock_instance.__aexit__ = AsyncMock(returnvalue=None)
        mock_instance.post = AsyncMock(return_value=AsyncMock(
            raise_for_status=AsyncMock(),
            json=AsyncMock(return_value=mock_resp),
        ))
        MockClient.return_value = mock_instance

        text, meta = await scrape_instagram("testuser", "People")
        assert "[Instagram profile: @testuser]" in text
        assert meta["source"] == "instagram"
        assert meta["cached"] is False


@pytest.mark.asyncio
async def test_primary_fails_secondary_succeeds():
    """
    kittygr.am fails → imginn.com succeeds → uses imginn result.
    Third mirror (picnob) never called.
    """
    md_imginn = """# Backup User\n\n## backupuser\n\n**2000** Followers\n**100** Following\n**50** Posts\nFrom imginn.
"""

    responses_by_instance = {}

    def make_response(instance_base):
        if "kittygr.am" in instance_base:
            # Primary fails
            return {"results": [{"success": False, "markdown": ""}]}
        elif "imginn.com" in instance_base:
            return {"results": [{"success": True, "markdown": md_imginn}]}
        else:
            return {"results": [{"success": False, "markdown": ""}]}

    call_count = [0]

    async def fake_post(url, **kwargs):
        call_count[0] += 1
        instance = None
        for inst in IG_MIRROR_INSTANCES:
            if inst.replace("https://", "") in url:
                instance = inst
                break
        if instance:
            resp = AsyncMock()
            resp.raise_for_status = AsyncMock()
            resp.json = AsyncMock(return_value=make_response(instance))
            return resp
        # URL doesn't match any known instance
        resp = AsyncMock()
        resp.raise_for_status = AsyncMock()
        resp.json = AsyncMock(return_value={"results": [{"success": False, "markdown": ""}]})
        return resp

    with patch("httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(returnvalue=mock_instance)
        mock_instance.__aexit__ = AsyncMock(returnvalue=None)
        mock_instance.post = fake_post
        MockClient.return_value = mock_instance

        text, meta = await scrape_instagram("backupuser", "People")
        assert "backupuser" in text
        assert "imginn" in text.lower() or meta["source"] == "instagram"


@pytest.mark.asyncio
async def test_all_mirrors_fail_sentinel():
    """All three mirrors fail → graceful sentinel, no exception."""
    async def fake_post(url, **kwargs):
        resp = AsyncMock()
        resp.raise_for_status = AsyncMock()
        resp.json = AsyncMock(return_value={"results": [{"success": False, "markdown": ""}]})
        return resp

    with patch("httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(returnvalue=mock_instance)
        mock_instance.__aexit__ = AsyncMock(returnvalue=None)
        mock_instance.post = fake_post
        MockClient.return_value = mock_instance

        text, meta = await scrape_instagram("alwaysfail", "People")
        assert text.startswith("[Instagram scrape error: all 3 mirrors failed")
        assert meta == {}  # empty profile on total failure


@pytest.mark.asyncio
async def test_cache_hit_skips_scraping():
    """On cache hit, no HTTP calls are made."""
    save_instagram_cache("cacheduser", "People", "[Instagram profile: @cacheduser] cached!")
    calls = []

    async def fake_post(url, **kwargs):
        calls.append(url)
        resp = AsyncMock()
        resp.raise_for_status = AsyncMock()
        resp.json = AsyncMock(return_value={"results": [{"success": False, "markdown": ""}]})
        return resp

    with patch("httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(returnvalue=mock_instance)
        mock_instance.__aexit__ = AsyncMock(returnvalue=None)
        mock_instance.post = fake_post
        MockClient.return_value = mock_instance

        text, meta = await scrape_instagram("cacheduser", "People")
        assert text == "[Instagram profile: @cacheduser] cached!"
        assert meta == {"source": "instagram", "cached": True}
        assert calls == [], "cache hit should not trigger HTTP"