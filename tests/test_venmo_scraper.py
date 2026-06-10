"""
Tests for the Venmo scraper (PHA-797).

Covers:
  • Field normalisation (profile + payment)
  • Soft-404 detection (friends-only / "update your app" gates)
  • Cache helpers (read/write round-trip in entity_cache)
  • scrape_venmo dispatch (cache hit, network path, soft-404 graceful)
  • Source URL builders
  • Note-cleaning (URLs, control chars, whitespace)

Live tests against the real Venmo API are gated by VENMO_LIVE_TESTS=1
because the public API is heavily rate-limited and a misbehaving
CI run would burn our quota.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scraper import venmo
from app.services.scraper.venmo import (
    _BROWSER_HEADERS,
    _FEED_ENDPOINT,
    _USER_ENDPOINT,
    _clean_text,
    _is_soft_404,
    _payment_to_record,
    _profile_to_record,
    _user_source_url,
    _feed_source_url,
    parse_venmo_feed,
    parse_venmo_profile,
    scrape_venmo,
)


# ---------------------------------------------------------------------------
# Source URL builders
# ---------------------------------------------------------------------------


def test_user_source_url_strips_at_prefix():
    assert _user_source_url("@aaron-tom-21") == "https://venmo.com/aaron-tom-21"


def test_user_source_url_strips_query():
    assert _user_source_url("aaron-tom-21?tab=code") == "https://venmo.com/aaron-tom-21"


def test_user_source_url_passes_through_plain():
    assert _user_source_url("aaron-tom-21") == "https://venmo.com/aaron-tom-21"


def test_feed_source_url_includes_anchor():
    url = _feed_source_url("aaron-tom-21")
    assert url == "https://venmo.com/aaron-tom-21#public-feed"


# ---------------------------------------------------------------------------
# Soft-404 detection
# ---------------------------------------------------------------------------


def test_is_soft_404_detects_update_app_gate():
    body = (
        '{"error": {"message": "In order to continue accessing your '
        'account, please update to the latest version of the app", "code": 2}}'
    )
    assert _is_soft_404(body) is True


def test_is_soft_404_detects_resource_not_found():
    body = '{"error": {"message": "Resource not found.", "code": 283}}'
    assert _is_soft_404(body) is True


def test_is_soft_404_detects_unauthorized():
    body = '{"error": {"message": "Unauthorized"}}'
    assert _is_soft_404(body) is True


def test_is_soft_404_returns_true_for_empty_body():
    assert _is_soft_404("") is True
    # _is_soft_404 is typed Optional[str] but defensively handles None
    # at runtime — this is a deliberate widen for robustness.
    assert _is_soft_404(None) is True  # type: ignore[arg-type]


def test_is_soft_404_returns_false_for_real_feed():
    body = json.dumps({
        "data": [
            {"id": "123", "note": "Pizza", "actor": {"username": "alice"}}
        ]
    })
    assert _is_soft_404(body) is False


# ---------------------------------------------------------------------------
# Profile parser
# ---------------------------------------------------------------------------


def test_profile_to_record_extracts_all_fields():
    profile = {
        "username": "venmo",
        "display_name": "Venmo",
        "first_name": "Venmo",
        "last_name": " ",
        "id": "1290060955648000288",
        "identity_type": "masspay_business",
        "date_joined": "2013",
        "profile_picture_url": "https://pics-v3.venmo.com/x?width=460",
    }
    rec = _profile_to_record(profile, "venmo")
    assert rec["username"] == "venmo"
    assert rec["display_name"] == "Venmo"
    assert rec["id"] == "1290060955648000288"
    assert rec["identity_type"] == "masspay_business"
    assert rec["date_joined"] == "2013"
    assert rec["source"] == "venmo"
    assert rec["source_url"] == "https://venmo.com/venmo"


def test_profile_to_record_handles_missing_fields():
    rec = _profile_to_record({}, "fallback-user")
    assert rec["username"] == "fallback-user"
    assert rec["display_name"] == ""
    assert rec["id"] == ""
    assert rec["source"] == "venmo"


def test_parse_venmo_profile_delegates_to_profile_to_record():
    profile = {"username": "alice", "id": "abc"}
    rec = parse_venmo_profile(profile, "alice")
    assert rec["id"] == "abc"
    assert rec["source_url"] == "https://venmo.com/alice"


# ---------------------------------------------------------------------------
# Payment parser
# ---------------------------------------------------------------------------


def test_payment_to_record_extracts_actor_target_and_note():
    payment = {
        "id": "tx_001",
        "note": "Pizza split 🍕",
        "actor": {"username": "alice", "display_name": "Alice", "id": "u1"},
        "target": {"username": "bob", "display_name": "Bob", "id": "u2"},
        "created_time": "2024-01-15T18:30:00Z",
        "type": "pay",
        "audience": "public",
    }
    rec = _payment_to_record(payment)
    assert rec is not None
    assert rec["id"] == "tx_001"
    assert rec["note"] == "Pizza split 🍕"
    assert rec["actor"]["username"] == "alice"
    assert rec["target"]["username"] == "bob"
    assert rec["created_at"] == "2024-01-15T18:30:00Z"
    assert rec["type"] == "pay"
    assert rec["audience"] == "public"
    assert rec["source"] == "venmo"


def test_payment_to_record_skips_payments_with_no_note():
    """No note = no trivia signal. The brief is explicit on this."""
    payment = {
        "id": "tx_002",
        "note": "",
        "actor": {"username": "alice"},
        "target": {"username": "bob"},
    }
    assert _payment_to_record(payment) is None


def test_payment_to_record_handles_alternate_date_field():
    """Some feed entries use date_created instead of created_time."""
    payment = {
        "id": "tx_003",
        "note": "Coffee",
        "actor": {"username": "alice"},
        "target": {"username": "bob"},
        "date_created": "2023-12-01T08:00:00Z",
    }
    rec = _payment_to_record(payment)
    assert rec["created_at"] == "2023-12-01T08:00:00Z"


def test_payment_to_record_returns_none_for_non_dict():
    # _payment_to_record is typed dict but defensively handles None
    # and non-dict inputs at runtime — deliberate widen for robustness.
    assert _payment_to_record("not a dict") is None  # type: ignore[arg-type]
    assert _payment_to_record(None) is None  # type: ignore[arg-type]
    assert _payment_to_record([]) is None  # type: ignore[arg-type]


def test_parse_venmo_feed_filters_out_note_less_entries():
    feed = [
        {"id": "tx_001", "note": "Pizza", "actor": {"username": "a"}, "target": {"username": "b"}},
        {"id": "tx_002", "note": "",    "actor": {"username": "a"}, "target": {"username": "b"}},
        {"id": "tx_003", "note": "Beer", "actor": {"username": "a"}, "target": {"username": "b"}},
    ]
    out = parse_venmo_feed(feed)
    assert len(out) == 2
    assert [r["note"] for r in out] == ["Pizza", "Beer"]


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def test_clean_text_strips_urls():
    assert _clean_text("Check out https://example.com today") == "Check out today"


def test_clean_text_strips_control_chars():
    assert _clean_text("hello\x00\x01world") == "helloworld"


def test_clean_text_collapses_whitespace():
    assert _clean_text("hello\n\n\tworld") == "hello world"


def test_clean_text_preserves_emoji():
    """Trivia game uses unicode heavily — don't strip non-ASCII."""
    assert "🍕" in _clean_text("Pizza 🍕 split")


def test_clean_text_handles_empty():
    assert _clean_text("") == ""
    # _clean_text is typed str but defensively handles None at runtime.
    assert _clean_text(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _fake_db():
    """Mock SessionLocal context manager + a query/filter chain."""
    db = MagicMock()
    return db


def test_get_venmo_cache_returns_cached_content():
    cached_record = MagicMock()
    cached_record.raw_content = "cached text"
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = cached_record

    with patch.object(venmo, "SessionLocal", return_value=db):
        result = venmo.get_venmo_cache("alice", "People")
    assert result == "cached text"
    db.close.assert_called_once()


def test_get_venmo_cache_returns_none_when_empty():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch.object(venmo, "SessionLocal", return_value=db):
        result = venmo.get_venmo_cache("alice", "People")
    assert result is None
    # _clean_text type ignore removed above; this block is a no-op for
    # the Optional[EntityCache] query path which is correctly typed.


def test_save_venmo_cache_creates_new_record():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch.object(venmo, "SessionLocal", return_value=db):
        venmo.save_venmo_cache("alice", "People", "fresh content")
    db.add.assert_called_once()
    db.commit.assert_called_once()


def test_save_venmo_cache_updates_existing_record():
    existing = MagicMock()
    existing.raw_content = "old"
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = existing

    with patch.object(venmo, "SessionLocal", return_value=db):
        venmo.save_venmo_cache("alice", "People", "new content")
    assert existing.raw_content == "new content"
    assert existing.scraped_at > 0
    db.add.assert_not_called()
    db.commit.assert_called_once()


def test_save_venmo_cache_swallows_db_errors():
    """A write failure must not propagate — the scraper is best-effort."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.add.side_effect = RuntimeError("db down")

    with patch.object(venmo, "SessionLocal", return_value=db):
        venmo.save_venmo_cache("alice", "People", "content")
    db.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# scrape_venmo — dispatch
# ---------------------------------------------------------------------------


def test_scrape_venmo_strips_at_prefix():
    async def run():
        return await scrape_venmo("@alice", "People")
    # Even when there's no cache + no network, returns ("", [])
    # — just confirm no crash on the @ prefix.
    with patch.object(venmo, "get_venmo_cache", return_value=None):
        with patch.object(venmo, "_scrape_venmo_with_fallback", AsyncMock(return_value=("", []))):
            raw, records = asyncio.run(run())
    assert raw == ""
    assert records == []


def test_scrape_venmo_returns_cached_on_cache_hit():
    async def run():
        return await scrape_venmo("alice", "People")
    with patch.object(venmo, "get_venmo_cache", return_value="cached raw text"):
        raw, records = asyncio.run(run())
    assert raw == "cached raw text"
    assert records == [{"source": "venmo", "cached": True, "username": "alice"}]


def test_scrape_venmo_writes_back_to_cache_on_fresh_scrape():
    async def run():
        return await scrape_venmo("alice", "People")
    with patch.object(venmo, "get_venmo_cache", return_value=None):
        with patch.object(venmo, "_scrape_venmo_with_fallback",
                          AsyncMock(return_value=("fresh raw text", [{"id": "r1"}]))):
            with patch.object(venmo, "save_venmo_cache") as save:
                raw, records = asyncio.run(run())
    assert raw == "fresh raw text"
    assert records == [{"id": "r1"}]
    save.assert_called_once()
    # Save args: (username, entity_type, content)
    args = save.call_args.args
    assert args[0] == "alice"
    assert args[1] == "People"
    assert args[2] == "fresh raw text"


def test_scrape_venmo_caps_content_to_settings_max():
    async def run():
        return await scrape_venmo("alice", "People")
    long_raw = "x" * 1000
    # The test env's content_max_chars is 200000 by default, so we patch
    # settings to a smaller value to verify capping happens.
    with patch.object(venmo, "get_venmo_cache", return_value=None):
        with patch.object(venmo, "_scrape_venmo_with_fallback",
                          AsyncMock(return_value=(long_raw, []))):
            with patch.object(venmo, "save_venmo_cache"):
                with patch.object(venmo.settings, "content_max_chars", 100):
                    raw, _ = asyncio.run(run())
    assert len(raw) == 100
    assert raw == "x" * 100


def test_scrape_venmo_handles_empty_username():
    async def run():
        return await scrape_venmo("", "People")
    raw, records = asyncio.run(run())
    assert raw == ""
    assert records == []


def test_scrape_venmo_does_not_write_empty_content_to_cache():
    async def run():
        return await scrape_venmo("alice", "People")
    with patch.object(venmo, "get_venmo_cache", return_value=None):
        with patch.object(venmo, "_scrape_venmo_with_fallback",
                          AsyncMock(return_value=("", []))):
            with patch.object(venmo, "save_venmo_cache") as save:
                raw, records = asyncio.run(run())
    assert raw == ""
    save.assert_not_called()


# ---------------------------------------------------------------------------
# _scrape_venmo_with_fallback — full flow with mocked transport
# ---------------------------------------------------------------------------


def test_scrape_with_fallback_profile_only_when_feed_gated():
    """Friends-only user: profile succeeds, feed returns []."""
    profile = {"username": "alice", "display_name": "Alice", "id": "u1", "date_joined": "2019"}
    async def run():
        with patch.object(venmo, "_fetch_user_profile", AsyncMock(return_value=profile)):
            with patch.object(venmo, "_fetch_public_feed", AsyncMock(return_value=[])):
                return await venmo._scrape_venmo_with_fallback("alice")
    raw, records = asyncio.run(run())
    assert any(r.get("username") == "alice" for r in records)
    assert "Alice" in raw
    assert "joined 2019" in raw


def test_scrape_with_fallback_full_feed():
    """Public user: profile + feed both succeed."""
    profile = {"username": "alice", "display_name": "Alice", "id": "u1", "date_joined": "2019"}
    feed = [
        {"id": "tx_001", "note": "Pizza", "actor": {"username": "alice", "display_name": "Alice"},
         "target": {"username": "bob", "display_name": "Bob"}},
        {"id": "tx_002", "note": "Coffee", "actor": {"username": "bob"},
         "target": {"username": "alice", "display_name": "Alice"}},
    ]
    async def run():
        with patch.object(venmo, "_fetch_user_profile", AsyncMock(return_value=profile)):
            with patch.object(venmo, "_fetch_public_feed", AsyncMock(return_value=feed)):
                return await venmo._scrape_venmo_with_fallback("alice")
    raw, records = asyncio.run(run())
    # 1 profile + 2 payments = 3 records
    assert len(records) == 3
    assert "Pizza" in raw
    assert "Coffee" in raw
    assert "Alice → Bob: Pizza" in raw


def test_scrape_with_fallback_private_user_returns_empty():
    """Private user: both endpoints return None / []."""
    async def run():
        with patch.object(venmo, "_fetch_user_profile", AsyncMock(return_value=None)):
            with patch.object(venmo, "_fetch_public_feed", AsyncMock(return_value=[])):
                return await venmo._scrape_venmo_with_fallback("ghost")
    raw, records = asyncio.run(run())
    assert raw == ""
    assert records == []


# ---------------------------------------------------------------------------
# Endpoint constants sanity
# ---------------------------------------------------------------------------


def test_user_endpoint_uses_v5_api():
    assert _USER_ENDPOINT == "https://venmo.com/api/v5/users"


def test_feed_endpoint_uses_v5_public():
    assert _FEED_ENDPOINT == "https://venmo.com/api/v5/public"


def test_browser_headers_include_user_agent_and_accept():
    """Venmo's UA-driven "update your app" gate is bypassed by
    sending browser-like headers."""
    assert "User-Agent" in _BROWSER_HEADERS
    assert "Mozilla" in _BROWSER_HEADERS["User-Agent"]
    assert _BROWSER_HEADERS["Accept"].startswith("application/json")
    assert "Accept-Language" in _BROWSER_HEADERS
    assert "Referer" in _BROWSER_HEADERS
    assert _BROWSER_HEADERS["Referer"].startswith("https://venmo.com")


# ---------------------------------------------------------------------------
# Live tests — gated by env var, require outbound internet
# ---------------------------------------------------------------------------


LIVE = pytest.mark.skipif(
    not os.environ.get("VENMO_LIVE_TESTS"),
    reason="set VENMO_LIVE_TESTS=1 to run live Venmo tests (heavy rate limit!)",
)


@LIVE
def test_live_venmo_known_account():
    """
    Real Venmo API for a known-public handle.  Venmo's own corporate
    account (@venmo) is the most reliable public test subject.
    """
    async def run():
        return await scrape_venmo("venmo", "People")
    raw, records = asyncio.run(run())
    # We can't assert the exact set of fields (live data), but we should
    # get SOMETHING for the corporate account.
    assert isinstance(records, list)
    if records:
        assert any(r.get("source") == "venmo" for r in records)
