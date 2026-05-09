"""
Tests for Steam scraper: resolve_steam_id, scrape_steam, cache roundtrip.
Run live network tests with: pytest -m live_network
Skip in CI without STEAM_API_KEY set.
"""
import os
import pytest
from app.services.scraper.steam import (
    resolve_steam_id,
    is_steam_id64,
    scrape_steam,
)
from app.config import settings


class TestIsSteamId64:
    def test_valid(self):
        assert is_steam_id64("76561197960287930") is True
        assert is_steam_id64("76561198281098754") is True

    def test_too_short(self):
        assert is_steam_id64("765611979602879") is False

    def test_too_long(self):
        assert is_steam_id64("765611979602879301") is False

    def test_wrong_prefix(self):
        assert is_steam_id64("12345678901234567") is False

    def test_non_digit(self):
        assert is_steam_id64("7656119796028792X") is False


class TestResolveSteamId:
    @pytest.mark.live_network
    @pytest.mark.asyncio
    async def test_resolve_steam_id_numeric(self):
        # Numeric SteamID64 passed through unchanged — no network
        result = await resolve_steam_id("76561197960287930")
        assert result == "76561197960287930"

    @pytest.mark.live_network
    @pytest.mark.asyncio
    async def test_resolve_steam_id_vanity_live(self):
        # Karl Jobst — fully public, no API key needed
        result = await resolve_steam_id("karljobst")
        assert result == "76561198281098754"

    @pytest.mark.live_network
    @pytest.mark.asyncio
    async def test_resolve_steam_id_url(self):
        # Community URL stripped and resolved
        result = await resolve_steam_id("https://steamcommunity.com/id/karljobst/")
        assert result == "76561198281098754"


class TestScrapeSteamNoKeyFallback:
    @pytest.mark.live_network
    @pytest.mark.asyncio
    async def test_scrape_steam_no_key_fallback(self):
        # Without STEAM_API_KEY: returns a minimal identity blob, does NOT raise
        api_key_before = settings.steam_api_key
        settings.steam_api_key = ""
        try:
            raw, posts = await scrape_steam("karljobst")
            assert "karljobst" in raw or "Karl" in raw
            assert posts[0]["source"] == "steam"
        finally:
            settings.steam_api_key = api_key_before


class TestScrapeSteamWithKey:
    @pytest.mark.skipif(
        not os.environ.get("STEAM_API_KEY"),
        reason="STEAM_API_KEY not set — skipping full scrape test",
    )
    @pytest.mark.asyncio
    async def test_scrape_steam_with_key_full(self):
        """Full pipeline with real key. Assert top-games header and at least one game name."""
        raw, posts = await scrape_steam("karljobst")
        assert "[Top games by playtime]" in raw
        # Should have some game names (appdetails resolved)
        lines = raw.split("\n")
        game_lines = [l for l in lines if " — " in l and "h" in l]
        assert len(game_lines) >= 1, f"Expected at least one game line, got: {raw[:500]}"


class TestScrapeSteamCacheRoundtrip:
    @pytest.mark.asyncio
    async def test_scrape_steam_cache_roundtrip(self, monkeypatch):
        """Second call to scrape_steam returns cached=True and does not re-hit network."""
        from unittest.mock import patch

        # Resolve vanity to avoid real HTTP call in the no-key fallback path
        async def mock_resolve_vanity_to_id(vanity_slug):
            return "76561198281098754"

        # Sync cache store — get_steam_cache is synchronous
        _cache: dict[tuple[str, str], str] = {}

        def mock_get(ename, etype):
            return _cache.get((ename, etype))

        def mock_save(ename, etype, content, surl):
            _cache[(ename, etype)] = content

        with patch(
            "app.services.scraper.steam.resolve_vanity_to_id",
            mock_resolve_vanity_to_id,
        ), patch(
            "app.services.scraper.steam.get_steam_cache",
            mock_get,
        ), patch(
            "app.services.scraper.steam.save_steam_cache",
            mock_save,
        ):
            api_key_before = settings.steam_api_key
            settings.steam_api_key = ""
            try:
                # First call — not cached
                raw1, posts1 = await scrape_steam("karljobst")
                assert posts1[0]["cached"] is False

                # Second call — cached
                raw2, posts2 = await scrape_steam("karljobst")
                assert posts2[0]["cached"] is True
                assert raw1 == raw2
            finally:
                settings.steam_api_key = api_key_before
