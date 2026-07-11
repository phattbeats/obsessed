"""
PHA-1348: YouTube scraper tests — Innertube primary, Data API v3 fallback, sentinel, cache.
Fixtures under tests/fixtures/youtube/ are trimmed real Innertube responses
(captured live against youtube.com — no login, no API key).
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import EntityCache, SessionLocal
from app.services.scraper.youtube import (
    scrape_youtube,
    search_youtube_channel,
    search_youtube_channel_innertube,
    search_youtube_channel_data_api,
    get_channel_uploads,
    get_channel_uploads_innertube,
    get_channel_uploads_data_api,
    get_video_metadata,
    get_video_metadata_innertube,
    get_video_metadata_data_api,
    get_youtube_cache,
    save_youtube_cache,
    _parse_channel_renderer,
    _parse_lockup_video,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "youtube"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def _mock_response(data: dict, status_ok: bool = True):
    res = MagicMock()
    if status_ok:
        res.raise_for_status = MagicMock()
    else:
        res.raise_for_status = MagicMock(side_effect=Exception("http error"))
    res.json = MagicMock(return_value=data)
    return res


@pytest.fixture(autouse=True)
def _clean_cache():
    db = SessionLocal()
    try:
        db.query(EntityCache).filter(
            EntityCache.source_url.like("https://www.youtube.com/%")
        ).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(EntityCache).filter(
            EntityCache.source_url.like("https://www.youtube.com/%")
        ).delete()
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Unit parsing helpers (against captured real Innertube shapes)
# ---------------------------------------------------------------------------

def test_parse_channel_renderer_from_fixture():
    fixture = _load_fixture("search_mrbeast.json")
    contents = (
        fixture["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]
        ["sectionListRenderer"]["contents"][0]["itemSectionRenderer"]["contents"]
    )
    cr = contents[0]["channelRenderer"]
    profile = _parse_channel_renderer(cr)
    assert profile["channel_id"] == "UCX6OQ3DkcsbYNE6H8uQQuVA"
    assert profile["title"] == "MrBeast"
    # YouTube swaps these two fields' apparent meaning in search results.
    assert profile["handle"] == "@MrBeast"
    assert "subscribers" in profile["subscribers"] or "M" in profile["subscribers"]
    assert profile["verified"] is True


def test_parse_lockup_video_from_fixture():
    fixture = _load_fixture("browse_uploads.json")
    items = (
        fixture["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][1]
        ["tabRenderer"]["content"]["richGridRenderer"]["contents"]
    )
    lockup = items[0]["richItemRenderer"]["content"]["lockupViewModel"]
    video = _parse_lockup_video(lockup)
    assert video["video_id"] == "iYlODtkyw_I"
    assert "Survive 30 Days" in video["title"]
    assert "views" in video["views"]
    assert video["published"]


def test_cache_roundtrip():
    save_youtube_cache("testchannel", "People", "youtube cached content", "UCabc123")
    assert get_youtube_cache("testchannel", "People") == "youtube cached content"


# ---------------------------------------------------------------------------
# End-to-end with mocked HTTP — search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_innertube_success():
    fixture = _load_fixture("search_mrbeast.json")

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = AsyncMock(return_value=_mock_response(fixture))
        MockClient.return_value = mock_inst

        raw, profile = await search_youtube_channel_innertube("MrBeast")
        assert profile["channel_id"] == "UCX6OQ3DkcsbYNE6H8uQQuVA"
        assert "MrBeast" in raw
        assert "✓" in raw


@pytest.mark.asyncio
async def test_search_innertube_fails_falls_back_to_data_api(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "youtube_api_key", "fake-key")

    search_resp = _mock_response({
        "items": [{"id": {"channelId": "UCdataapi123"}, "snippet": {"channelId": "UCdataapi123"}}]
    })
    channels_resp = _mock_response({
        "items": [{
            "snippet": {"title": "Data API Channel", "customUrl": "@dataapichannel", "description": "desc"},
            "statistics": {"subscriberCount": "12345"},
        }]
    })

    calls = []

    async def track_post(url, **kwargs):
        calls.append(url)
        raise Exception("innertube down")

    async def track_get(url, **kwargs):
        if "search" in url:
            return search_resp
        return channels_resp

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = track_post
        mock_inst.get = track_get
        MockClient.return_value = mock_inst

        raw, profile = await search_youtube_channel("MrBeast")
        assert profile["channel_id"] == "UCdataapi123"
        assert profile["subscribers"] == "12345"
        assert "Data API Channel" in raw


@pytest.mark.asyncio
async def test_search_all_sources_fail_sentinel(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "youtube_api_key", "")  # no fallback key configured

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = AsyncMock(side_effect=Exception("innertube down"))
        mock_inst.get = AsyncMock(side_effect=Exception("data api down"))
        MockClient.return_value = mock_inst

        raw, profile = await scrape_youtube("nobodychannel", "People")
        assert raw.startswith("[YouTube scrape error:")
        assert profile == {}


# ---------------------------------------------------------------------------
# End-to-end with mocked HTTP — uploads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_channel_uploads_innertube_success():
    fixture = _load_fixture("browse_uploads.json")

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = AsyncMock(return_value=_mock_response(fixture))
        MockClient.return_value = mock_inst

        uploads = await get_channel_uploads_innertube("UCX6OQ3DkcsbYNE6H8uQQuVA")
        assert len(uploads) >= 1
        assert uploads[0]["video_id"]
        assert uploads[0]["title"]


@pytest.mark.asyncio
async def test_channel_uploads_falls_back_to_data_api(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "youtube_api_key", "fake-key")

    data_api_resp = _mock_response({
        "items": [{
            "id": {"videoId": "vid123"},
            "snippet": {"title": "Fallback video", "publishedAt": "2026-01-01T00:00:00Z"},
        }]
    })

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = AsyncMock(side_effect=Exception("innertube down"))
        mock_inst.get = AsyncMock(return_value=data_api_resp)
        MockClient.return_value = mock_inst

        uploads = await get_channel_uploads("UCsomeid")
        assert uploads == [{
            "video_id": "vid123",
            "title": "Fallback video",
            "views": "",
            "published": "2026-01-01T00:00:00Z",
        }]


# ---------------------------------------------------------------------------
# End-to-end with mocked HTTP — video metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_video_metadata_innertube_success():
    fixture = _load_fixture("player_video.json")

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = AsyncMock(return_value=_mock_response(fixture))
        MockClient.return_value = mock_inst

        meta = await get_video_metadata_innertube("zRtGL0-5rg4")
        assert meta["video_id"] == "zRtGL0-5rg4"
        assert meta["channel"] == "MrBeast"
        assert meta["category"] == "Entertainment"


@pytest.mark.asyncio
async def test_video_metadata_falls_back_to_data_api(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "youtube_api_key", "fake-key")

    data_api_resp = _mock_response({
        "items": [{
            "snippet": {
                "title": "Fallback Video Title",
                "channelTitle": "Some Channel",
                "channelId": "UCfallback",
                "description": "desc",
                "publishedAt": "2026-02-02T00:00:00Z",
            },
            "statistics": {"viewCount": "999"},
        }]
    })

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = AsyncMock(side_effect=Exception("innertube down"))
        mock_inst.get = AsyncMock(return_value=data_api_resp)
        MockClient.return_value = mock_inst

        meta = await get_video_metadata("vidfallback")
        assert meta["title"] == "Fallback Video Title"
        assert meta["view_count"] == "999"


# ---------------------------------------------------------------------------
# scrape_youtube — full pipeline + cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_youtube_full_pipeline():
    search_fixture = _load_fixture("search_mrbeast.json")
    browse_fixture = _load_fixture("browse_uploads.json")

    async def track_post(url, **kwargs):
        if "search" in url:
            return _mock_response(search_fixture)
        if "browse" in url:
            return _mock_response(browse_fixture)
        raise Exception("unexpected endpoint")

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = track_post
        MockClient.return_value = mock_inst

        raw, meta = await scrape_youtube("MrBeast", "People")
        assert "MrBeast" in raw
        assert "Recent uploads" in raw
        assert meta.get("source") == "youtube"
        assert meta.get("cached") is False


@pytest.mark.asyncio
async def test_scrape_youtube_cache_hit_skips_http():
    save_youtube_cache("cachedchannel", "People", "[YouTube channel: cached] test!", "UCcached")
    calls = []

    async def track_post(url, **kwargs):
        calls.append(("post", url))
        raise Exception("should not be called")

    async def track_get(url, **kwargs):
        calls.append(("get", url))
        raise Exception("should not be called")

    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.__aexit__.return_value = None
        mock_inst.post = track_post
        mock_inst.get = track_get
        MockClient.return_value = mock_inst

        raw, meta = await scrape_youtube("cachedchannel", "People")
        assert raw == "[YouTube channel: cached] test!"
        assert meta == {"source": "youtube", "cached": True}
        assert calls == [], f"cache hit should not call HTTP: {calls}"
