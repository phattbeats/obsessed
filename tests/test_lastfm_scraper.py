"""
Tests for the last.fm scraper: no-key fallback, fixture-driven pipeline,
period handling, and cache roundtrip.
Run live network tests with: pytest -m live_network
Skip in CI without LASTFM_API_KEY set (no anonymous last.fm endpoint exists).
"""
import json
import os
from pathlib import Path

import pytest

from app.services.scraper.lastfm import scrape_lastfm
from app.config import settings

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "lastfm" / "sample_user.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text())


def _mock_lastfm_get_factory():
    """Return an async fn mimicking _lastfm_get, keyed by the fixture's method names."""

    async def _mock_lastfm_get(method, username, api_key, extra=None):
        return FIXTURE[method]

    return _mock_lastfm_get


class TestScrapeLastfmNoKeyFallback:
    @pytest.mark.asyncio
    async def test_scrape_lastfm_no_key_fallback(self):
        # Without LASTFM_API_KEY: returns a minimal identity blob, does NOT raise, no network.
        api_key_before = settings.lastfm_api_key
        settings.lastfm_api_key = ""
        try:
            raw, posts = await scrape_lastfm("some_user")
            assert "some_user" in raw
            assert "No API key" in raw
            assert posts[0]["source"] == "lastfm"
            assert posts[0]["cached"] is False
        finally:
            settings.lastfm_api_key = api_key_before


class TestScrapeLastfmFixturePipeline:
    @pytest.mark.asyncio
    async def test_scrape_lastfm_builds_raw_content(self, monkeypatch):
        from unittest.mock import patch

        _cache: dict = {}

        def mock_get(ename, etype):
            return _cache.get((ename, etype))

        def mock_save(ename, etype, content, surl):
            _cache[(ename, etype)] = content

        with patch(
            "app.services.scraper.lastfm._lastfm_get", _mock_lastfm_get_factory()
        ), patch(
            "app.services.scraper.lastfm.get_lastfm_cache", mock_get
        ), patch(
            "app.services.scraper.lastfm.save_lastfm_cache", mock_save
        ):
            api_key_before = settings.lastfm_api_key
            settings.lastfm_api_key = "fake-test-key"
            try:
                raw, posts = await scrape_lastfm("rj", period="overall")
            finally:
                settings.lastfm_api_key = api_key_before

        assert posts[0]["source"] == "lastfm"
        assert posts[0]["cached"] is False
        assert "Richard Jones" in raw
        assert "Total scrobbles: 50234" in raw
        assert "[Top artists — all-time]" in raw
        assert "Radiohead — 512 plays" in raw
        assert "[Top tracks — all-time]" in raw
        assert "Everything In Its Right Place by Radiohead — 45 plays" in raw
        assert "[Top albums — all-time]" in raw
        assert "Kid A by Radiohead — 60 plays" in raw
        assert "[Recent scrobbles]" in raw
        assert "Idioteque by Radiohead (now playing)" in raw
        assert "Pyramid Song by Radiohead" in raw

    @pytest.mark.asyncio
    async def test_scrape_lastfm_invalid_period_defaults_to_overall(self, monkeypatch):
        from unittest.mock import patch

        with patch(
            "app.services.scraper.lastfm._lastfm_get", _mock_lastfm_get_factory()
        ), patch(
            "app.services.scraper.lastfm.get_lastfm_cache", lambda *a: None
        ), patch(
            "app.services.scraper.lastfm.save_lastfm_cache", lambda *a: None
        ):
            api_key_before = settings.lastfm_api_key
            settings.lastfm_api_key = "fake-test-key"
            try:
                raw, _ = await scrape_lastfm("rj", period="not-a-real-period")
            finally:
                settings.lastfm_api_key = api_key_before

        assert "[Top artists — all-time]" in raw


class TestScrapeLastfmCacheRoundtrip:
    @pytest.mark.asyncio
    async def test_scrape_lastfm_cache_roundtrip(self):
        """Second call to scrape_lastfm returns cached=True without re-hitting the API."""
        from unittest.mock import patch

        _cache: dict = {}
        call_count = {"n": 0}

        async def counting_mock_lastfm_get(method, username, api_key, extra=None):
            call_count["n"] += 1
            return FIXTURE[method]

        def mock_get(ename, etype):
            return _cache.get((ename, etype))

        def mock_save(ename, etype, content, surl):
            _cache[(ename, etype)] = content

        with patch(
            "app.services.scraper.lastfm._lastfm_get", counting_mock_lastfm_get
        ), patch(
            "app.services.scraper.lastfm.get_lastfm_cache", mock_get
        ), patch(
            "app.services.scraper.lastfm.save_lastfm_cache", mock_save
        ):
            api_key_before = settings.lastfm_api_key
            settings.lastfm_api_key = "fake-test-key"
            try:
                raw1, posts1 = await scrape_lastfm("rj")
                assert posts1[0]["cached"] is False
                calls_after_first = call_count["n"]
                assert calls_after_first > 0

                raw2, posts2 = await scrape_lastfm("rj")
                assert posts2[0]["cached"] is True
                assert raw1 == raw2
                # No additional API calls made on the cached path.
                assert call_count["n"] == calls_after_first
            finally:
                settings.lastfm_api_key = api_key_before


class TestScrapeLastfmWithKeyLive:
    @pytest.mark.live_network
    @pytest.mark.skipif(
        not os.environ.get("LASTFM_API_KEY"),
        reason="LASTFM_API_KEY not set — skipping live last.fm scrape test",
    )
    @pytest.mark.asyncio
    async def test_scrape_lastfm_with_key_full(self):
        """Full pipeline with a real key against last.fm's public 'rj' test account."""
        api_key_before = settings.lastfm_api_key
        settings.lastfm_api_key = os.environ["LASTFM_API_KEY"]
        try:
            raw, posts = await scrape_lastfm("rj")
        finally:
            settings.lastfm_api_key = api_key_before
        assert posts[0]["source"] == "lastfm"
        assert "[last.fm profile] rj" in raw
