"""PHA-789 regression: news scraper URL encoding."""
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.services.scraper import news as news_mod


@pytest.mark.asyncio
async def test_search_news_url_encoding_no_attribute_error():
    """
    search_news must not call httpx.utils.encode_url_component (httpx 0.27
    removed httpx.utils). URL construction happens outside the broad
    try/except in search_news, so a regression would surface as
    AttributeError to the caller, not a silent [] return.
    """
    mock_resp = MagicMock()
    mock_resp.text = "<rss><channel></channel></rss>"
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.object(news_mod.httpx, "AsyncClient", return_value=mock_client):
        result = await news_mod.search_news("test query")

    assert isinstance(result, list)
    called_url = mock_client.get.await_args.args[0]
    assert "q=test+query" in called_url, f"expected quote_plus encoding, got: {called_url}"


@pytest.mark.asyncio
async def test_search_news_handles_special_chars():
    """quote_plus must encode '&', '=', and spaces so the query string is valid."""
    mock_resp = MagicMock()
    mock_resp.text = "<rss><channel></channel></rss>"
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.object(news_mod.httpx, "AsyncClient", return_value=mock_client):
        result = await news_mod.search_news("AT&T earnings")

    assert isinstance(result, list)
    called_url = mock_client.get.await_args.args[0]
    assert "AT%26T" in called_url, f"'&' must be percent-encoded, got: {called_url}"
