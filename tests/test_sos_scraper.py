"""PHA-794 regression: Ohio SOS scraper hits the real businesssearchapi
endpoint and returns structured rows.

The live API tests are skipped by default — they require FlareSolverr at
10.0.0.100:8191 and outbound internet. Enable with
`SOS_LIVE_TESTS=1 pytest tests/test_sos_scraper.py`.

The default suite covers the pure URL-encoding logic and the response
normalization so a regression in either is caught in CI without network.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch, AsyncMock

import pytest

from app.services.scraper import sos as sos_mod
from app.services.scraper.sos import (
    OHIO_API_BASE,
    OHIO_STATUS_ACTIVE,
    OHIO_STATUS_ALL,
    _encode_business_name,
    _encode_owner_name,
    _normalize_ohio_row,
    search_by_owner,
    search_sos_entities,
)


# --- URL encoding (no network) ------------------------------------------


def test_encode_business_name_basic():
    assert _encode_business_name("Buckeye LLC", OHIO_STATUS_ALL) == "BUCKEYE%20LLC_X"


def test_encode_business_name_active_status():
    assert _encode_business_name("Acme Corp", OHIO_STATUS_ACTIVE) == "ACME%20CORP_A"


def test_encode_business_name_substitutes_special_chars():
    # Per the UI script: punctuation outside [a-zA-Z0-9-_%& ] is stripped,
    # then '-', '_', '%', '&' get replaced by the SOS token table.
    assert _encode_business_name("AT&T Inc.", OHIO_STATUS_ALL) == "AT$A29T%20INC_X"


def test_encode_business_name_strips_invalid_chars():
    # '*' is not in the allowed set, so it must be dropped.
    assert _encode_business_name("Foo*Bar LLC", OHIO_STATUS_ALL) == "FOOBAR%20LLC_X"


def test_encode_owner_name_uppercases_and_space_encodes():
    assert _encode_owner_name("CT Corporation System") == "CT%20CORPORATION%20SYSTEM"


def test_encode_owner_name_strips_invalid_chars():
    assert _encode_owner_name("LaRose, Frank!") == "LAROSE%20FRANK"


# --- Response normalization (no network) --------------------------------


def test_normalize_ohio_row_canonical_shape():
    raw = {
        "result_count": 1,
        "processing_id": "200705801456",
        "business_name": "PCS & BUILD, LLC",
        "business_type": "DOMESTIC LIMITED LIABILITY COMPANY",
        "business_location": "-",
        "state_name": "-",
        "county_name": "-",
        "status": "Active",
        "tran_code": "LCA",
        "charter_num": "1680646",
        "agent_effective_date": "2019-05-22T16:08:18Z",
        "effect_date": "2007-02-20T09:00:00Z",
        "agent_name": " CT CORPORATION SYSTEM",
        "agent_status": "Active",
    }
    row = _normalize_ohio_row(raw, source_url="https://example/api/X")
    assert row["entity_name"] == "PCS & BUILD, LLC"
    assert row["entity_id"] == "1680646"
    assert row["jurisdiction"] == "Ohio"
    assert row["status"] == "Active"
    assert row["formation_date"] == "2007-02-20"
    assert row["entity_type"] == "DOMESTIC LIMITED LIABILITY COMPANY"
    assert row["agent_name"] == "CT CORPORATION SYSTEM"
    assert row["agent_effective_date"] == "2019-05-22"
    assert row["agent_status"] == "Active"
    assert row["processing_id"] == "200705801456"
    assert row["source_url"] == "https://example/api/X"


def test_normalize_handles_missing_optional_fields():
    raw = {
        "business_name": "X CO",
        "charter_num": "1",
        "status": "Active",
        "effect_date": "2000-01-01T00:00:00Z",
    }
    row = _normalize_ohio_row(raw, source_url="")
    assert row["agent_name"] is None
    assert row["agent_effective_date"] is None
    assert row["county_name"] is None


# --- Mocked end-to-end ---------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_clearance_cache():
    """Each test starts with no cached Cloudflare clearance so mocks land
    on a deterministic call path."""
    sos_mod._clearance_cache.clear()
    yield
    sos_mod._clearance_cache.clear()


@pytest.mark.asyncio
async def test_search_sos_entities_ohio_returns_structured_rows():
    fake_payload = {
        "data": [
            {
                "result_count": 1,
                "processing_id": "200705801456",
                "business_name": "BUCKEYE STATE TEST LLC",
                "business_type": "DOMESTIC LIMITED LIABILITY COMPANY",
                "status": "Active",
                "tran_code": "LCA",
                "charter_num": "9999999",
                "effect_date": "2024-01-15T00:00:00Z",
            }
        ]
    }

    async def _fake_clearance(force=False):
        return {"cf_clearance": "x", "__cf_bm": "y"}, "Mozilla/5.0 test"

    async def _fake_get(self, url):
        from httpx import Response, Request

        return Response(
            200,
            json=fake_payload,
            request=Request("GET", url),
        )

    with patch.object(sos_mod, "_get_clearance", _fake_clearance), patch(
        "httpx.AsyncClient.get", _fake_get
    ):
        rows = await search_sos_entities("Ohio", "Buckeye LLC")

    assert len(rows) == 1
    row = rows[0]
    assert row["entity_name"] == "BUCKEYE STATE TEST LLC"
    assert row["entity_id"] == "9999999"
    assert row["status"] == "Active"
    assert row["formation_date"] == "2024-01-15"
    assert row["jurisdiction"] == "Ohio"
    assert row["source_url"].startswith(f"{OHIO_API_BASE}/NS_")


@pytest.mark.asyncio
async def test_search_by_owner_ohio_uses_agent_endpoint():
    fake_payload = {
        "data": [
            {
                "business_name": "OWNED CO LLC",
                "charter_num": "1234567",
                "status": "Active",
                "effect_date": "2010-01-01T00:00:00Z",
                "agent_name": " JANE DOE",
                "agent_status": "Active",
                "agent_effective_date": "2015-06-01T00:00:00Z",
            }
        ]
    }
    seen_urls: list[str] = []

    async def _fake_clearance(force=False):
        return {"cf_clearance": "x"}, "ua"

    async def _fake_get(self, url):
        from httpx import Response, Request

        seen_urls.append(url)
        return Response(200, json=fake_payload, request=Request("GET", url))

    with patch.object(sos_mod, "_get_clearance", _fake_clearance), patch(
        "httpx.AsyncClient.get", _fake_get
    ):
        rows = await search_by_owner("Ohio", "Jane Doe")

    assert len(rows) == 1
    assert rows[0]["entity_id"] == "1234567"
    assert rows[0]["agent_name"] == "JANE DOE"
    assert rows[0]["owner"] == "Jane Doe"
    assert any(u.startswith(f"{OHIO_API_BASE}/AE_") for u in seen_urls)


@pytest.mark.asyncio
async def test_search_returns_empty_on_empty_input():
    rows = await search_sos_entities("Ohio", "")
    assert rows == []
    rows = await search_sos_entities("Ohio", "   ")
    assert rows == []
    rows = await search_by_owner("Ohio", "")
    assert rows == []


@pytest.mark.asyncio
async def test_search_swallows_cloudflare_wall_to_empty():
    from app.services.scraper.flaresolverr import CloudflareWallError

    async def _fake_clearance(force=False):
        raise CloudflareWallError("still walled", 0)

    with patch.object(sos_mod, "_get_clearance", _fake_clearance):
        rows = await search_sos_entities("Ohio", "Anything LLC")

    assert rows == []


# --- Live API test (opt-in) ---------------------------------------------


_LIVE = os.environ.get("SOS_LIVE_TESTS") == "1"


@pytest.mark.asyncio
@pytest.mark.skipif(not _LIVE, reason="SOS_LIVE_TESTS!=1 (requires FlareSolverr + internet)")
async def test_live_search_returns_real_ohio_llc():
    """End-to-end smoke against businesssearchapi.ohiosos.gov.

    Asserts the acceptance criterion from PHA-794: at least one structured
    row with a non-empty entity_id, status, and formation_date.
    """
    rows = await search_sos_entities("Ohio", "Buckeye LLC")
    assert rows, "expected at least one match for 'Buckeye LLC'"
    sample = rows[0]
    assert sample["entity_id"], f"missing entity_id: {sample}"
    assert sample["status"], f"missing status: {sample}"
    assert sample["formation_date"], f"missing formation_date: {sample}"
    assert sample["jurisdiction"] == "Ohio"
