"""PHA-821 — Franklin County Probate Court scraper tests.

Default suite: mocked HTML, no network.
Live tests: PROBATE_LIVE_TESTS=1 pytest tests/test_probate_scraper.py
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

from app.services.scraper.probate import _parse_rows, _next_page_param, search_probate_by_name

# ---------------------------------------------------------------------------
# Fixture HTML — two rows, one with a next-page link
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """\
<html><body>
<table border="1">
<tr bgcolor="#07528B">
  <th>Case Number</th><th>Case Name</th><th>Type</th>
  <th>SubType</th><th>Status</th><th>Opened</th><th>Closed</th>
</tr>
<tr bgcolor="lightblue">
  <td><a href="http://probatesearch.franklincountyohio.gov/netdata/PBCaseTypeE.ndm/ESTATE_DETAIL?caseno=436430;;">436430</a></td>
  <td><font size="-2">SMITH, JOHN A.</font></td>
  <td><font size="-2">ESTATE</font></td>
  <td><font size="-2">RELEASE FROM ADMIN WITH WILL</font></td>
  <td>99</td>
  <td>05/11/1995</td>
  <td>07/18/1996</td>
</tr>
<tr bgcolor="White">
  <td><a href="http://probatesearch.franklincountyohio.gov/netdata/PBCaseTypeG.ndm/GUARD_DETAIL?caseno=569272;;">569272</a></td>
  <td><font size="-2">SMITH, JOHN ALBERT</font></td>
  <td><font size="-2">GUARDIANSHIP ADULT</font></td>
  <td><font size="-2">PERSON ONLY</font></td>
  <td>99</td>
  <td>10/28/2014</td>
  <td>05/20/2020</td>
</tr>
</table>
<a href="input?stringf=SMITH, JOHN ALBERT*=567148!=">Next Case Names &gt;&gt;</a>
</body></html>
"""

_SAMPLE_HTML_LAST_PAGE = """\
<html><body>
<table border="1">
<tr bgcolor="lightblue">
  <td><a href="http://probatesearch.franklincountyohio.gov/netdata/PBCaseTypeE.ndm/ESTATE_DETAIL?caseno=999999;;">999999</a></td>
  <td>SMITH, JOHNNIE</td>
  <td>ESTATE</td>
  <td>FULL ADMINISTRATION WITHOUT WILL</td>
  <td>01</td>
  <td>01/15/2023</td>
  <td></td>
</tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Pure parsing tests (no network)
# ---------------------------------------------------------------------------


def test_parse_rows_extracts_all_fields():
    rows = _parse_rows(_SAMPLE_HTML, source_url="https://example/test")
    assert len(rows) == 2

    r0 = rows[0]
    assert r0["case_no"] == "436430"
    assert r0["case_name"] == "SMITH, JOHN A."
    assert r0["case_type"] == "ESTATE"
    assert r0["subtype"] == "RELEASE FROM ADMIN WITH WILL"
    assert r0["status"] == "99"
    assert r0["filed_date"] == "05/11/1995"
    assert r0["closed_date"] == "07/18/1996"
    assert "ESTATE_DETAIL" in r0["detail_url"]
    assert r0["source_url"] == "https://example/test"

    r1 = rows[1]
    assert r1["case_no"] == "569272"
    assert r1["case_type"] == "GUARDIANSHIP ADULT"
    assert "GUARD_DETAIL" in r1["detail_url"]


def test_parse_rows_https_normalises_detail_url():
    rows = _parse_rows(_SAMPLE_HTML, source_url="")
    for row in rows:
        assert row["detail_url"].startswith("https://"), row["detail_url"]


def test_parse_rows_empty_on_no_match():
    rows = _parse_rows("<html><body><table></table></body></html>", source_url="")
    assert rows == []


def test_next_page_param_found():
    val = _next_page_param(_SAMPLE_HTML)
    assert val == "SMITH, JOHN ALBERT*=567148!="


def test_next_page_param_none_on_last_page():
    val = _next_page_param(_SAMPLE_HTML_LAST_PAGE)
    assert val is None


def test_parse_rows_open_case_has_empty_closed_date():
    rows = _parse_rows(_SAMPLE_HTML_LAST_PAGE, source_url="")
    assert len(rows) == 1
    assert rows[0]["closed_date"] == ""


# ---------------------------------------------------------------------------
# Mocked async tests
# ---------------------------------------------------------------------------


def _make_response(html: str, url: str) -> httpx.Response:
    req = httpx.Request("GET", url)
    return httpx.Response(200, text=html, request=req)


@pytest.mark.asyncio
async def test_search_returns_empty_on_blank_last_name():
    rows = await search_probate_by_name("")
    assert rows == []

    rows = await search_probate_by_name("   ")
    assert rows == []


@pytest.mark.asyncio
async def test_search_single_page_result():
    async def _fake_get(self, url, *, params=None, **kwargs):
        return _make_response(_SAMPLE_HTML_LAST_PAGE, str(url))

    with patch("httpx.AsyncClient.get", _fake_get):
        rows = await search_probate_by_name("Smith", "Johnnie")

    assert len(rows) == 1
    assert rows[0]["case_no"] == "999999"
    assert rows[0]["case_type"] == "ESTATE"


@pytest.mark.asyncio
async def test_search_follows_pagination():
    call_count = 0

    async def _fake_get(self, url, *, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(_SAMPLE_HTML, str(url))
        return _make_response(_SAMPLE_HTML_LAST_PAGE, str(url))

    with patch("httpx.AsyncClient.get", _fake_get):
        rows = await search_probate_by_name("Smith", "John", max_pages=5)

    assert call_count == 2
    assert len(rows) == 3  # 2 from page 1, 1 from page 2


@pytest.mark.asyncio
async def test_search_respects_max_pages():
    async def _fake_get(self, url, *, params=None, **kwargs):
        # Always return a page with a next-page link to test the cap
        return _make_response(_SAMPLE_HTML, str(url))

    with patch("httpx.AsyncClient.get", _fake_get):
        rows = await search_probate_by_name("Smith", max_pages=2)

    # max_pages=2 → 2 pages × 2 rows each = 4
    assert len(rows) == 4


@pytest.mark.asyncio
async def test_search_swallows_http_error():
    async def _fake_get(self, url, *, params=None, **kwargs):
        raise httpx.ConnectError("connection refused")

    with patch("httpx.AsyncClient.get", _fake_get):
        rows = await search_probate_by_name("Smith", "John")

    assert rows == []


# ---------------------------------------------------------------------------
# Live integration test (opt-in, requires internet)
# ---------------------------------------------------------------------------

_LIVE = os.environ.get("PROBATE_LIVE_TESTS") == "1"


@pytest.mark.asyncio
@pytest.mark.skipif(not _LIVE, reason="PROBATE_LIVE_TESTS!=1")
async def test_live_estate_smith_john():
    """Smoke test: SMITH, JOHN A. — estate case 436430, opened 05/11/1995."""
    rows = await search_probate_by_name("Smith", "John")
    assert rows, "expected at least one result for Smith, John"
    case_nos = [r["case_no"] for r in rows]
    assert "436430" in case_nos, f"case 436430 not found in {case_nos[:10]}"
    row = next(r for r in rows if r["case_no"] == "436430")
    assert row["case_type"] == "ESTATE"
    assert row["filed_date"] == "05/11/1995"


@pytest.mark.asyncio
@pytest.mark.skipif(not _LIVE, reason="PROBATE_LIVE_TESTS!=1")
async def test_live_guardianship_returns_structured_row():
    """Smoke test: SMITH, JOHN ALBERT — guardianship case 569272."""
    rows = await search_probate_by_name("Smith", "John Albert")
    case_nos = [r["case_no"] for r in rows]
    assert "569272" in case_nos, f"case 569272 not found in {case_nos[:10]}"
    row = next(r for r in rows if r["case_no"] == "569272")
    assert "GUARDIANSHIP" in row["case_type"]
    assert row["filed_date"] == "10/28/2014"
