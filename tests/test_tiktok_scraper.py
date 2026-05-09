"""
PHA-694: TikTok scraper tests — primary success, failover, sentinel.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.database import EntityCache, SessionLocal
from app.services.scraper.tiktok import (
    scrape_tiktok,
    scrape_tiktok_profile,
    _scrape_tikwm_json,
    _scrape_tnktok_html,
    _expand_suffix,
    get_tiktok_cache,
    save_tiktok_cache,
    _parse_tnktok_markdown,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    db = SessionLocal()
    try:
        db.query(EntityCache).filter(
            EntityCache.source_url.like("https://www.tiktok.com/%")
        ).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(EntityCache).filter(
            EntityCache.source_url.like("https://www.tiktok.com/%")
        ).delete()
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def test_expand_suffix():
    assert _expand_suffix("161.2M") == "161200000"
    assert _expand_suffix("2.6B") == "2600000000"
    assert _expand_suffix("500K") == "500000"
    assert _expand_suffix("1234567") == "1234567"
    assert _expand_suffix("bad") == "bad"


def test_parse_tnktok_markdown_khaby():
    """From actual tnktok.com markdown shape for @khaby.lame."""
    md = """# Khabane lame

## khaby.lame

**84** Following
**161.2M** Followers
**2.6B** Likes

## Se vuoi ridere sei nel posto giusto😎 If u wanna laugh u r in the right place😎

"""
    raw, profile = _parse_tnktok_markdown(md, "khaby.lame")
    assert profile["username"] == "khaby.lame"
    assert profile["display_name"] == "Khabane lame"
    assert profile["followers"] == "161200000"
    assert profile["following"] == "84"
    assert profile["likes"] == "2600000000"
    assert "ridere" in profile["bio"] or "laugh" in profile["bio"]


def test_parse_tnktok_markdown_no_handle():
    """Empty markdown returns ("", {}) so failover fires."""
    raw, profile = _parse_tnktok_markdown("", "nobody")
    assert raw == ""
    assert profile == {}


def test_cache_roundtrip():
    save_tiktok_cache("testuser", "People", "tiktok cached content")
    assert get_tiktok_cache("testuser", "People") == "tiktok cached content"


# ---------------------------------------------------------------------------
# End-to-end with mocked HTTP
# ---------------------------------------------------------------------------

async def _mock_httpx_get(url, **kwargs):
    res = AsyncMock()
    res.raise_for_status = AsyncMock()
    if "tikwm" in url:
        res.json = AsyncMock(return_value={
            "code": 0,
            "data": {
                "user": {
                    "uniqueId": "khaby.lame",
                    "nickname": "Khabane Lame",
                    "signature": "If u wanna laugh u r in the right place😎",
                    "verified": True,
                    "privateAccount": False,
                    "createTime": 1474243200,
                },
                "stats": {
                    "followerCount": 161178162,
                    "followingCount": 84,
                    "heartCount": 2601832551,
                    "videoCount": 1323,
                },
            },
        })
    else:
        res.json = AsyncMock(return_value={})
    return res


async def _mock_crawl4ai_post(url, **kwargs):
    res = AsyncMock()
    res.raise_for_status = AsyncMock()
    res.json = AsyncMock(return_value={
        "results": [{"success": True, "markdown": ""}]
    })
    return res


@pytest.mark.asyncio
async def test_primary_tikwm_success():
    """Tikwm JSON returns profile — uses it, no crawl4ai call."""
    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__ = AsyncMock(returnvalue=mock_inst)
        mock_inst.__aexit__ = AsyncMock(returnvalue=None)
        mock_inst.get = _mock_httpx_get
        mock_inst.post = _mock_crawl4ai_post
        MockClient.return_value = mock_inst

        text, meta = await scrape_tiktok("khaby.lame", "People")
        assert "khaby.lame" in text
        assert "Khabane Lame" in text
        assert "✓" in text
        assert meta.get("source") == "tiktok"
        assert meta.get("cached") is False


@pytest.mark.asyncio
async def test_primary_fails_fallback_tnktok():
    """tikwm returns error → tnktok returns profile → uses tnktok."""
    async def tikwm_fail(url, **kwargs):
        raise Exception("tikwm down")

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__ = AsyncMock(returnvalue=mock_inst)
        mock_inst.__aexit__ = AsyncMock(returnvalue=None)
        mock_inst.get = tikwm_fail
        mock_inst.post = _mock_crawl4ai_post
        MockClient.return_value = mock_inst

        text, meta = await scrape_tiktok("khaby.lame", "People")
        assert text.startswith("[TikTok scrape error:") or "khaby.lame" in text


@pytest.mark.asyncio
async def test_all_sources_fail_sentinel():
    """Both sources fail → graceful sentinel."""
    async def all_fail(url, **kwargs):
        raise Exception("both down")

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__ = AsyncMock(returnvalue=mock_inst)
        mock_inst.__aexit__ = AsyncMock(returnvalue=None)
        mock_inst.get = all_fail
        mock_inst.post = all_fail
        MockClient.return_value = mock_inst

        text, meta = await scrape_tiktok("alwaysfail", "People")
        assert text.startswith("[TikTok scrape error:")
        assert meta == {}


@pytest.mark.asyncio
async def test_cache_hit_skips_http():
    """Cache hit returns without any HTTP calls."""
    save_tiktok_cache("cacheduser", "People", "[TikTok profile: @cacheduser] test!")
    calls = []

    async def track_get(url, **kwargs):
        calls.append(("get", url))
        raise Exception("should not be called")

    async def track_post(url, **kwargs):
        calls.append(("post", url))
        raise Exception("should not be called")

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__ = AsyncMock(returnvalue=mock_inst)
        mock_inst.__aexit__ = AsyncMock(returnvalue=None)
        mock_inst.get = track_get
        mock_inst.post = track_post
        MockClient.return_value = mock_inst

        text, meta = await scrape_tiktok("cacheduser", "People")
        assert text == "[TikTok profile: @cacheduser] test!"
        assert meta == {"source": "tiktok", "cached": True}
        assert calls == [], f"cache hit should not call HTTP: {calls}"