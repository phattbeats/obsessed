"""
PHA-700: Pinterest scraper failover chain tests.
Tests the three-source chain: pinterest-dl → pinscrape → crawl4ai.
Mirrors the PHA-682 Instagram failover test pattern.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.scraper.pinterest import (
    scrape_pinterest,
    _scrape_pinterest_dl,
    _scrape_pinscrape,
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
    res = AsyncMock()
    res.raise_for_status = AsyncMock()
    res.json = AsyncMock(return_value={
        "results": [{
            "success": True,
            "markdown": {
                "raw_markdown": "# Test User\n[Board One](https://pinterest.com/u/b1) 10 Pins\n[Board Two](https://pinterest.com/u/b2) 5 Pins",
            }
        }]
    })
    return res


@pytest.mark.asyncio
async def test_primary_pinterest_dl_succeeds():
    """pinterest-dl returns profile → used, no further calls."""
    with patch("subprocess.run") as MockRun:
        MockRun.return_value = type("R", (), {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        })()

        # Patch os.path.exists to simulate the output file
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", side_effect=lambda f: type("F", (), {
                "__enter__": lambda s: type("C", (), {
                    "read": lambda: '{"profile":{"name":"Test User","handle":"testuser","boards":[{"name":"Board One","url":"https://pinterest.com/u/b1","pin_count":"10"},{"name":"Board Two","url":"https://pinterest.com/u/b2","pin_count":"5"}]}}'
                })(),
                "__exit__": lambda *a: None,
            })()):
                with patch("tempfile.TemporaryDirectory") as MockTmp:
                    MockTmp.return_value.__enter__ = lambda s: "/tmp/fake"
                    MockTmp.return_value.__exit__ = lambda *a: None
                    text, boards = await scrape_pinterest("testuser")

    assert "Test User" in text
    assert len(boards) == 2
    assert boards[0]["name"] == "Board One"


@pytest.mark.asyncio
async def test_pinterest_dl_fails_pinscrape_succeeds():
    """pinterest-dl raises → pinscrape returns profile → used."""
    async def pdl_fail(*args, **kwargs):
        raise RuntimeError("pinterest-dl not found")

    async def pinscrape_ok(*args, **kwargs):
        return '{"username":"testuser","profile":{"name":"Scrape User"},"boards":[{"name":"Pin Board","url":"https://pinterest.com/u/p1","pin_count":"20"}]}'

    with patch("subprocess.run") as MockRun:
        def run_side_effect(cmd, *a, **kw):
            if "pinterest-dl" in cmd[0] or "pinterest-dl" in " ".join(cmd):
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": "not found"})()
            return type("R", (), {"returncode": 0, "stdout": '{"username":"testuser","profile":{"name":"Scrape User"},"boards":[{"name":"Pin Board","url":"https://pinterest.com/u/p1","pin_count":"20"}]}', "stderr": ""})()

        MockRun.side_effect = run_side_effect

        text, boards = await scrape_pinterest("testuser")

    assert "Scrape User" in text
    assert boards[0]["name"] == "Pin Board"


@pytest.mark.asyncio
async def test_all_sources_fail_sentinel():
    """pinterest-dl + pinscrape both fail → crawl4ai fails → sentinel."""
    async def always_fail(*args, **kwargs):
        raise RuntimeError("all tools unavailable")

    with patch("subprocess.run", side_effect=always_fail):
        with patch("httpx.AsyncClient") as MockClient:
            mock_inst = AsyncMock()
            mock_inst.__aenter__ = AsyncMock(returnvalue=mock_inst)
            mock_inst.__aexit__ = AsyncMock(returnvalue=None)
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
        mock_inst.__aenter__ = AsyncMock(returnvalue=mock_inst)
        mock_inst.__aexit__ = AsyncMock(returnvalue=None)
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