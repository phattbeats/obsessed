"""
PHA-700: Pinterest scraper failover chain tests.
Tests the two-source chain: pinterest-dl → crawl4ai.
Mirrors the PHA-682 Instagram failover test pattern.
"""
from unittest.mock import AsyncMock, Mock, mock_open, patch

import pytest

from app.services.scraper.pinterest import (
    scrape_pinterest,
    _scrape_pinterest_dl,
    _scrape_pinterest_crawl4ai,
)


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def test_scrape_pinterest_signature():
    """Public function signature unchanged."""
    import inspect
    sig = inspect.signature(scrape_pinterest)
    assert list(sig.parameters) == ["handle"]


# ---------------------------------------------------------------------------
# E2E failover chain with mocked internals
# ---------------------------------------------------------------------------

async def _mock_client_success(url, **kwargs):
    # httpx Response.json() and .raise_for_status() are sync — use Mock, not AsyncMock,
    # or production code (`data = resp.json()`) receives a coroutine instead of dict.
    res = Mock()
    res.raise_for_status = Mock()
    res.json = Mock(return_value={
        "results": [{
            "success": True,
            "markdown": {
                # URLs must use www.pinterest.com to match the board regex.
                "raw_markdown": "# Test User\n[Board One](https://www.pinterest.com/u/b1) 10 Pins\n[Board Two](https://www.pinterest.com/u/b2) 5 Pins",
            }
        }]
    })
    return res


@pytest.mark.asyncio
async def test_primary_pinterest_dl_succeeds():
    """pinterest-dl returns profile → used, no further calls."""
    profile_json = (
        '{"profile":{"name":"Test User","handle":"testuser","boards":['
        '{"name":"Board One","url":"https://pinterest.com/u/b1","pin_count":"10"},'
        '{"name":"Board Two","url":"https://pinterest.com/u/b2","pin_count":"5"}'
        ']}}'
    )

    with patch("subprocess.run") as MockRun, \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=profile_json)):
        MockRun.return_value = type("R", (), {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        })()
        text, boards = await scrape_pinterest("testuser")

    assert "Test User" in text
    assert len(boards) == 2
    assert boards[0]["name"] == "Board One"


@pytest.mark.asyncio
async def test_pinterest_dl_fails_crawl4ai_succeeds():
    """pinterest-dl raises → crawl4ai fallback returns profile → used."""
    with patch("subprocess.run") as MockRun:
        MockRun.side_effect = RuntimeError("pinterest-dl not found")

        with patch("httpx.AsyncClient") as MockClient:
            mock_inst = AsyncMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=None)
            mock_inst.post = _mock_client_success
            MockClient.return_value = mock_inst

            text, boards = await scrape_pinterest("testuser")

    assert "Test User" in text
    assert boards[0]["name"] == "Board One"


@pytest.mark.asyncio
async def test_all_sources_fail_sentinel():
    """pinterest-dl + crawl4ai both fail → sentinel."""
    async def always_fail(*args, **kwargs):
        raise RuntimeError("all tools unavailable")

    with patch("subprocess.run", side_effect=always_fail):
        with patch("httpx.AsyncClient") as MockClient:
            mock_inst = AsyncMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=None)
            mock_inst.post = AsyncMock(side_effect=Exception("crawl4ai down"))
            MockClient.return_value = mock_inst

            text, boards = await scrape_pinterest("alwaysfail")

    assert text.startswith("[Pinterest scrape error: all sources failed for @alwaysfail")
    assert boards == []


@pytest.mark.asyncio
async def test_cache_hit_skips_http():
    """No-op placeholder — pinterest.py has no native cache (unlike reddit/instagram)."""
    pass


# ---------------------------------------------------------------------------
# Crawl4ai fallback (isolated test)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crawl4ai_fallback_parses_markdown():
    """_scrape_pinterest_crawl4ai parses raw markdown correctly."""
    with patch("httpx.AsyncClient") as MockClient:
        mock_inst = AsyncMock()
        mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_inst.__aexit__ = AsyncMock(return_value=None)
        mock_inst.post = _mock_client_success
        MockClient.return_value = mock_inst

        text, boards = await _scrape_pinterest_crawl4ai("testuser")

    assert "Test User" in text
    assert len(boards) == 2
    assert boards[0]["pin_count"] == "10"


# ---------------------------------------------------------------------------
# generate_questions unchanged (signature test)
# ---------------------------------------------------------------------------

def test_generate_questions_signature():
    import inspect
    from app.services.scraper.pinterest import generate_questions
    sig = inspect.signature(generate_questions)
    assert list(sig.parameters) == ["profile_id", "raw_content", "name"]