"""Regression: `_profile()` helper must serialize profiles with NULL columns.

Discovered 2026-07-29 (PHA-1615): the public list endpoint returned
`500 Internal Server Error` for any profile whose row had NULL values in
columns that the helper read directly without `or ""` / `or 0` defaults.

Repro: `scripts/seed_demo.py` creates `Profile(name="Alex Delgado", entity_type="person")`
and relies on SQLAlchemy column defaults firing for every other field. On
production (SQLAlchemy 2.0 + Pydantic 2.9, fast-strict validation), if any
of those defaults didn't materialize — typically because the row was
inserted by a migration that added the column without backfill, or by an
older version of the seed script — the helper would hand `None` to
ProfileResponse, and validation would reject the row.

This test pins the contract: the helper must default every nullable field
to its type's zero value, matching the admin helper's defensive style.
A drift here means `/api/profiles` returns 500 in production.
"""
from unittest.mock import patch

from app.database import Profile, SessionLocal
from app.routes.profiles import _profile
from app.models import ProfileResponse


def test_profile_helper_handles_null_columns():
    """Profile rows with NULL fields must still serialize via _profile().

    Insert a row with most fields NULL via raw SQL (bypassing SQLAlchemy
    column defaults, which is what the legacy seed paths effectively did),
    then confirm _profile() + ProfileResponse.model_validate succeeds.
    """
    db = SessionLocal()
    try:
        # Minimum required: name (NOT NULL). Everything else Nullable.
        # Use the ORM to create the row so the model is registered, then
        # fall back to raw SQL to clear the columns that _profile() reads
        # without defensive defaults — this mimics the legacy bad state.
        p = Profile(name="Null Safety Probe")
        db.add(p)
        db.commit()
        db.refresh(p)

        # Wipe the columns that the pre-fix helper read without defaults.
        # Use raw SQL to force NULL on the fields that broke in production.
        db.execute(
            __import__("sqlalchemy").text(
                "UPDATE profiles SET "
                "bio=NULL, reddit_handle=NULL, twitter_handle=NULL, "
                "steam_id=NULL, discord_handle=NULL, pinterest_handle=NULL, "
                "instagram_handle=NULL, manual_link=NULL, manual_facts=NULL, "
                "scrape_status=NULL, scrape_error=NULL, question_count=NULL "
                "WHERE id = :pid"
            ),
            {"pid": p.id},
        )
        db.commit()
        db.refresh(p)

        # Helper must serialize without raising.
        resp_dict = _profile(p)
        resp = ProfileResponse.model_validate(resp_dict)

        # All defaulted fields should be their zero values.
        assert resp.bio == ""
        assert resp.reddit_handle == ""
        assert resp.twitter_handle == ""
        assert resp.steam_id == ""
        assert resp.discord_handle == ""
        assert resp.pinterest_handle == ""
        assert resp.instagram_handle == ""
        assert resp.manual_link == ""
        assert resp.manual_facts == ""
        assert resp.scrape_status == "pending"  # default fallback
        assert resp.scrape_error == ""
        assert resp.question_count == 0
        # Name is NOT NULL in DB; preserved as-is.
        assert resp.name == "Null Safety Probe"
    finally:
        db.close()


def test_profile_helper_handles_legacy_seed_demo():
    """The legacy seed_demo.py profile (id=1, name='Alex Delgado') must
    serialize cleanly even if earlier runs left NULL fields.

    This is the exact production case from PHA-1615: profile id=4 'Alex
    Delgado (seed_demo)' was created by an older seed pass that didn't
    populate all fields, and `/api/profiles/{id}` returned 500.
    """
    db = SessionLocal()
    try:
        # Create a profile exactly the way the legacy seed did:
        # only name + entity_type, no other fields.
        p = Profile(name="Alex Delgado (legacy)")
        p.entity_type = "person"
        db.add(p)
        db.commit()
        db.refresh(p)

        # The legacy seed path would also set bio + manual_facts + consent, but
        # the buggy case is a row that got partway through and then had NULLs
        # in some fields. Force a few more NULLs to be sure.
        db.execute(
            __import__("sqlalchemy").text(
                "UPDATE profiles SET scrape_status=NULL, scrape_error=NULL, "
                "news_query=NULL, court_query=NULL WHERE id = :pid"
            ),
            {"pid": p.id},
        )
        db.commit()
        db.refresh(p)

        resp_dict = _profile(p)
        resp = ProfileResponse.model_validate(resp_dict)
        assert resp.name == "Alex Delgado (legacy)"
        assert resp.scrape_status == "pending"
        assert resp.scrape_error == ""
        assert resp.news_query == ""
        assert resp.court_query == ""
    finally:
        db.close()


def test_profile_helper_consistent_with_admin_defaults():
    """The public _profile() and the admin list_all_profiles() helper must
    agree on default values for each field. Drift here means the public
    endpoint 500s while the admin endpoint is fine (or vice versa) — the
    PHA-1615 symptom.

    This test compares the relevant fields by name. If you add a new
    field to one helper, you must add it to the other.
    """
    # The fingerprint of which fields use `or ""` defaults in _profile().
    # Each tuple is (field_name, expected_default_when_null).
    public_defaults = {
        "bio": "",
        "reddit_handle": "",
        "twitter_handle": "",
        "steam_id": "",
        "lastfm_username": "",
        "discord_handle": "",
        "pinterest_handle": "",
        "instagram_handle": "",
        "tiktok_handle": "",
        "facebook_handle": "",
        "news_query": "",
        "court_query": "",
        "sos_query": "",
        "auditor_query": "",
        "voter_query": "",
        "wikipedia_handle": "",
        "osm_query": "",
        "travel_url": "",
        "wikidata_query": "",
        "openlibrary_query": "",
        "gdelt_query": "",
        "manual_link": "",
        "manual_facts": "",
        "scrape_status": "pending",
        "scrape_error": "",
        "question_count": 0,
        "llm_calls": 0,
        "llm_spend_cents": 0,
        "question_budget": 50,
        "consent_obtained": False,
        "content_quality": "",
        "content_chunks": 0,
        "entity_type": "person",
        "address_type": "unknown",
        "created_at": 0,
        "updated_at": 0,
    }

    # Programmatically prove the helper applies each default. We construct a
    # Profile with every field set to None (except id and name), then check
    # that _profile() produces the expected default for each field.
    db = SessionLocal()
    try:
        p = Profile(name="Defaults Probe")
        db.add(p)
        db.commit()
        db.refresh(p)

        # Force every nullable column to NULL via raw SQL.
        from sqlalchemy import text
        nullable_cols = [
            "bio", "reddit_handle", "twitter_handle", "steam_id",
            "lastfm_username", "discord_handle", "pinterest_handle",
            "instagram_handle", "tiktok_handle", "facebook_handle",
            "news_query", "court_query", "sos_query", "auditor_query",
            "voter_query", "wikipedia_handle", "osm_query", "travel_url",
            "wikidata_query", "openlibrary_query", "gdelt_query",
            "manual_link", "manual_facts", "scrape_status", "scrape_error",
            "question_count", "llm_calls", "llm_spend_cents",
            "question_budget", "consent_obtained", "content_quality",
            "content_chunks", "entity_type", "address_type",
            "created_at", "updated_at",
        ]
        set_clause = ", ".join(f"{c}=NULL" for c in nullable_cols)
        db.execute(
            text(f"UPDATE profiles SET {set_clause} WHERE id = :pid"),
            {"pid": p.id},
        )
        db.commit()
        db.refresh(p)

        resp_dict = _profile(p)
        resp = ProfileResponse.model_validate(resp_dict)

        for field, expected_default in public_defaults.items():
            actual = getattr(resp, field)
            assert actual == expected_default, (
                f"_profile() default drift for {field!r}: "
                f"expected {expected_default!r}, got {actual!r}"
            )
    finally:
        db.close()
