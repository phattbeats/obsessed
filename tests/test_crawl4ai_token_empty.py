"""
Regression test for PHA-790: Don't set 'Bearer ' Authorization header
when CRAWL4AI_TOKEN is empty.

When settings.crawl4ai_token is empty (or ''), _crawl4ai_headers() must
return {} — not {"Authorization": "Bearer "} — so that httpx does not raise
httpx.LocalProtocolError: Illegal header value b'Bearer '.
"""
from unittest.mock import AsyncMock, MagicMock, patch

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
        # httpx.Response.json() and .raise_for_status() are SYNC; only client.post() is awaitable.
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={
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

        # Should succeed without raising. If the empty-token bug regressed, httpx would have
        # raised LocalProtocolError before .post() returned, and the except clause would have
        # produced "[crawl4ai error: ...]" instead.
        assert "Example" in text or "Test content" in text, f"Unexpected result: {text!r}"
        assert meta.get("url") == "https://example.com"

        # Confirm the header passed to httpx contained NO Authorization key when token is empty.
        call_kwargs = mock_instance.post.call_args.kwargs
        assert call_kwargs.get("headers") == {}, (
            f"Expected empty headers when token is '', got {call_kwargs.get('headers')!r}"
        )