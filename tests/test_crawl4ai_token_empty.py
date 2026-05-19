"""
Regression test for PHA-790: Don't set 'Bearer ' Authorization header
when CRAWL4AI_TOKEN is empty.

When settings.crawl4ai_token is empty (or ''), _crawl4ai_headers() must
return {} — not {"Authorization": "Bearer "} — so that httpx does not raise
httpx.LocalProtocolError: Illegal header value b'Bearer '.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services.scraper.crawl4ai import crawl4ai_scrape, _crawl4ai_headers


class TestCrawl4AiHeadersEmptyToken:
    """Verify _crawl4ai_headers() and crawl4ai_scrape() behave correctly when token is empty."""

    def test_crawl4ai_headers_empty_string(self):
        """_crawl4ai_headers() returns {} when token is ''."""
        with patch.object(settings, "crawl4ai_token", ""):
            headers = _crawl4ai_headers()
        assert headers == {}, f"Expected {{}}, got {headers!r}"

    def test_crawl4ai_headers_none(self):
        """_crawl4ai_headers() returns {} when token is None."""
        with patch.object(settings, "crawl4ai_token", None):
            headers = _crawl4ai_headers()
        assert headers == {}, f"Expected {{}}, got {headers!r}"

    @patch("app.services.scraper.crawl4ai.httpx.AsyncClient")
    async def test_crawl4ai_scrape_no_token_no_error(self, MockAsyncClient):
        """
        crawl4ai_scrape() with empty token does not raise httpx.LocalProtocolError
        on the Authorization header.

        The bug was: f"Bearer {settings.crawl4ai_token}" → "Bearer " (trailing space)
        when token was empty, which httpx rejects with:
        httpx.LocalProtocolError: Illegal header value b'Bearer '
        """
        mock_instance = MockAsyncClient.return_value.__aenter__.return_value
        mock_response = AsyncMock()
        mock_response.raise_for_status = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "results": [{
                "url": "https://example.com",
                "markdown": {"raw_markdown": "# Example\n\nTest content."},
                "title": "Example",
                "description": "An example page.",
                "word_count": 3,
            }]
        })
        mock_instance.post = AsyncMock(return_value=mock_response)

        with patch.object(settings, "crawl4ai_token", ""):
            text, meta = await crawl4ai_scrape("https://example.com")

        # Should succeed without raising
        assert "Example" in text or "Test content" in text
        assert meta.get("url") == "https://example.com"