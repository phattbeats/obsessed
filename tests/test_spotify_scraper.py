"""
Tests for the Spotify scraper/OAuth module: PKCE helpers, token exchange,
token refresh, not-linked fallback, fixture-driven scrape pipeline, and
cache roundtrip.
"""
import base64
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import Profile, SessionLocal
from app.services.scraper.spotify import (
    build_authorize_url,
    ensure_fresh_token,
    exchange_code_for_token,
    generate_pkce_pair,
    scrape_spotify,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "spotify" / "sample_user.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text())


def _mock_spotify_get_factory():
    """Return an async fn mimicking _spotify_get, keyed by endpoint path."""

    async def _mock_spotify_get(endpoint, access_token, params=None):
        return FIXTURE[endpoint]

    return _mock_spotify_get


def _make_linked_profile(expires_at: int | None = None) -> int:
    db = SessionLocal()
    try:
        p = Profile(
            name="Richard Jones",
            entity_type="person",
            spotify_access_token="AT-old",
            spotify_refresh_token="RT-old",
            spotify_token_expires_at=expires_at if expires_at is not None else int(time.time()) + 3600,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


class TestPkceHelpers:
    def test_generate_pkce_pair_challenge_matches_verifier(self):
        code_verifier, code_challenge = generate_pkce_pair()
        assert 43 <= len(code_verifier) <= 128
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert code_challenge == expected
        # Base64url + no padding: no '+', '/', or '=' characters.
        assert "+" not in code_challenge and "/" not in code_challenge and "=" not in code_challenge

    def test_build_authorize_url_contains_required_params(self):
        from app.config import settings

        with patch.object(settings, "spotify_client_id", "test-client-id"), patch.object(
            settings, "spotify_redirect_uri", "http://localhost:8000/api/profiles/spotify/callback"
        ):
            url = build_authorize_url("state123", "challenge456")
        assert url.startswith("https://accounts.spotify.com/authorize?")
        assert "client_id=test-client-id" in url
        assert "state=state123" in url
        assert "code_challenge=challenge456" in url
        assert "code_challenge_method=S256" in url
        assert "user-top-read" in url
        # PKCE is a public-client flow — no secret should ever appear in the URL.
        assert "secret" not in url.lower()


class TestExchangeCodeForToken:
    @pytest.mark.asyncio
    @patch("app.services.scraper.spotify.httpx.AsyncClient")
    async def test_exchange_code_for_token_posts_verifier_not_secret(self, MockAsyncClient):
        mock_instance = MockAsyncClient.return_value.__aenter__.return_value
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={
            "access_token": "AT-new", "refresh_token": "RT-new", "expires_in": 3600,
        })
        mock_instance.post = AsyncMock(return_value=mock_response)

        token_data = await exchange_code_for_token("auth-code", "verifier-xyz")

        assert token_data["access_token"] == "AT-new"
        call_kwargs = mock_instance.post.call_args.kwargs
        posted = call_kwargs["data"]
        assert posted["code_verifier"] == "verifier-xyz"
        assert posted["grant_type"] == "authorization_code"
        assert "client_secret" not in posted


class TestEnsureFreshToken:
    @pytest.mark.asyncio
    async def test_ensure_fresh_token_returns_existing_when_not_expired(self):
        profile_id = _make_linked_profile(expires_at=int(time.time()) + 3600)
        with patch("app.services.scraper.spotify._refresh_access_token") as mock_refresh:
            token = await ensure_fresh_token(profile_id)
        mock_refresh.assert_not_called()
        assert token == "AT-old"

    @pytest.mark.asyncio
    async def test_ensure_fresh_token_refreshes_when_expired(self):
        profile_id = _make_linked_profile(expires_at=int(time.time()) - 10)
        mock_refresh = AsyncMock(return_value={"access_token": "AT-refreshed", "expires_in": 3600})
        with patch("app.services.scraper.spotify._refresh_access_token", mock_refresh):
            token = await ensure_fresh_token(profile_id)
        mock_refresh.assert_called_once_with("RT-old")
        assert token == "AT-refreshed"

        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == profile_id).first()
            assert p.spotify_access_token == "AT-refreshed"
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_ensure_fresh_token_not_linked_returns_none(self):
        db = SessionLocal()
        try:
            p = Profile(name="Unlinked Person", entity_type="person")
            db.add(p)
            db.commit()
            db.refresh(p)
            profile_id = p.id
        finally:
            db.close()

        token = await ensure_fresh_token(profile_id)
        assert token is None


class TestScrapeSpotifyNotLinked:
    @pytest.mark.asyncio
    async def test_scrape_spotify_returns_empty_when_not_linked(self):
        db = SessionLocal()
        try:
            p = Profile(name="Unlinked Person 2", entity_type="person")
            db.add(p)
            db.commit()
            db.refresh(p)
            profile_id = p.id
        finally:
            db.close()

        raw, posts = await scrape_spotify(profile_id)
        assert raw == ""
        assert posts[0]["source"] == "spotify"
        assert posts[0]["linked"] is False


class TestScrapeSpotifyFixturePipeline:
    @pytest.mark.asyncio
    async def test_scrape_spotify_builds_raw_content(self):
        profile_id = _make_linked_profile()
        _cache: dict = {}

        def mock_get(ename, etype):
            return _cache.get((ename, etype))

        def mock_save(ename, etype, content, surl):
            _cache[(ename, etype)] = content

        with patch(
            "app.services.scraper.spotify._spotify_get", _mock_spotify_get_factory()
        ), patch(
            "app.services.scraper.spotify.get_spotify_cache", mock_get
        ), patch(
            "app.services.scraper.spotify.save_spotify_cache", mock_save
        ):
            raw, posts = await scrape_spotify(profile_id)

        assert posts[0]["source"] == "spotify"
        assert posts[0]["cached"] is False
        assert "[Spotify profile] Richard Jones" in raw
        assert "[Top artists — last 6 months]" in raw
        assert "Radiohead — genres: art rock, alternative rock" in raw
        assert "[Top tracks — last 6 months]" in raw
        assert "Everything In Its Right Place by Radiohead (from Kid A)" in raw
        assert "[Playlists]" in raw
        assert "Rainy Day — 42 tracks" in raw
        assert "Shared Mix — 15 tracks (by Someone Else)" in raw

        # The linked profile's own account owns "Rainy Day" — no "(by ...)" suffix.
        assert "Rainy Day — 42 tracks (by" not in raw


class TestScrapeSpotifyCacheRoundtrip:
    @pytest.mark.asyncio
    async def test_scrape_spotify_cache_roundtrip(self):
        """Second call to scrape_spotify returns cached=True without re-hitting the API."""
        profile_id = _make_linked_profile()
        _cache: dict = {}
        call_count = {"n": 0}

        async def counting_mock_spotify_get(endpoint, access_token, params=None):
            call_count["n"] += 1
            return FIXTURE[endpoint]

        def mock_get(ename, etype):
            return _cache.get((ename, etype))

        def mock_save(ename, etype, content, surl):
            _cache[(ename, etype)] = content

        with patch(
            "app.services.scraper.spotify._spotify_get", counting_mock_spotify_get
        ), patch(
            "app.services.scraper.spotify.get_spotify_cache", mock_get
        ), patch(
            "app.services.scraper.spotify.save_spotify_cache", mock_save
        ):
            raw1, posts1 = await scrape_spotify(profile_id)
            assert posts1[0]["cached"] is False
            calls_after_first = call_count["n"]
            assert calls_after_first > 0

            raw2, posts2 = await scrape_spotify(profile_id)
            assert posts2[0]["cached"] is True
            assert raw1 == raw2
            assert call_count["n"] == calls_after_first
