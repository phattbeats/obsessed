"""Regression: _profile() must serialize rows with NULL columns without raising.

Repro: profile id=4 ("Alex Delgado (seed_demo)") triggered 500s on
GET /api/profiles and GET /api/profiles/{id}. The seed script inserts a row
with only `name`, `entity_type`, `bio`, `manual_facts`, and `consent_obtained`
populated; every other column relies on SQLAlchemy `default=...`, which only
fires at insert time. If the column is added later via `ALTER TABLE ...
DEFAULT ''` (or a row is migrated from a pre-existing DB), those columns can
be NULL in SQLite. The ProfileResponse model is strict (non-Optional str/int
on every field), so the previous `_profile()` raised ValidationError on read.

This test simulates a NULL-heavy row by inserting a Profile and then
explicitly NULLing every text/integer column via raw SQL, then verifies the
public `_profile()` helper serializes it. PHA-1615.
"""
import json

from sqlalchemy import text

from app.database import Profile, SessionLocal
from app.routes.profiles import _profile


def _null_all_text_and_int_columns(db, profile_id: int) -> None:
    """NULL every nullable text/integer column on the given profile.

    Mirrors what the seed_demo path looks like at runtime in the deployed
    Obsessed instance when a row pre-dates the relevant ALTER TABLE migration.
    """
    nullables = [
        "bio", "reddit_handle", "twitter_handle", "steam_id", "lastfm_username",
        "discord_handle", "pinterest_handle", "threads_handle",
        "instagram_handle", "tiktok_handle", "facebook_handle",
        "google_places_handle", "news_query", "court_query", "sos_query",
        "auditor_query", "voter_query", "wikipedia_handle", "osm_query",
        "travel_url", "wikidata_query", "openlibrary_query", "gdelt_query",
        "entity_type", "manual_link", "manual_facts",
        "scrape_status", "scrape_error", "raw_content",
        "question_count", "llm_calls", "llm_spend_cents",
        "question_budget", "consent_token",
        "content_quality", "content_chunks", "address_type",
        "created_at", "updated_at",
    ]
    for col in nullables:
        db.execute(text(f"UPDATE profiles SET {col} = NULL WHERE id = :pid"),
                   {"pid": profile_id})


def test_profile_helper_handles_null_columns():
    """Simulate the seed_demo NULL-row scenario; _profile() must not raise."""
    db = SessionLocal()
    try:
        p = Profile(name="Alex Delgado (seed_demo)")
        db.add(p)
        db.commit()
        db.refresh(p)

        _null_all_text_and_int_columns(db, p.id)
        db.commit()

        # Re-read through a fresh session to avoid stale identity-map values.
        db.expire_all()
        fresh = db.query(Profile).filter(Profile.id == p.id).first()
        assert fresh is not None

        resp = _profile(fresh)  # the actual fix being tested
        assert resp.name == "Alex Delgado (seed_demo)"
        assert resp.bio == ""
        assert resp.scrape_status == "pending"
        assert resp.scrape_error == ""
        assert resp.question_count == 0
        assert resp.llm_calls == 0
        assert resp.llm_spend_cents == 0
        assert resp.question_budget == 50
        assert resp.consent_obtained is False
        assert resp.content_chunks == 0
        assert resp.entity_type == "person"
        assert resp.address_type == "unknown"
        assert resp.created_at == 0
        assert resp.updated_at == 0
    finally:
        db.close()


def test_profile_helper_normal_row_still_works():
    """Sanity: a fully-populated row round-trips through _profile() unchanged."""
    db = SessionLocal()
    try:
        p = Profile(name="Ada Lovelace", bio="mathematician",
                    entity_type="person", consent_obtained=True,
                    scrape_status="done", question_count=42,
                    llm_calls=3, llm_spend_cents=12,
                    question_budget=50, content_chunks=80,
                    content_quality="rich", address_type="unknown",
                    created_at=1700000000, updated_at=1700000001)
        db.add(p)
        db.commit()
        db.refresh(p)

        resp = _profile(p)
        assert resp.name == "Ada Lovelace"
        assert resp.bio == "mathematician"
        assert resp.consent_obtained is True
        assert resp.question_count == 42
        assert resp.llm_calls == 3
        assert resp.llm_spend_cents == 12
        assert resp.content_quality == "rich"
        assert resp.content_chunks == 80
        assert resp.created_at == 1700000000
        assert resp.updated_at == 1700000001
    finally:
        db.close()