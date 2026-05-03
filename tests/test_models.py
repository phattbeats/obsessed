"""Schema-drift guard: ProfileResponse must be constructible from a Profile row.

If a column is added to `Profile` (database.py) but not mirrored on
`ProfileResponse` (models.py) — or vice versa — this test fails before
production does. PHA-342 was exactly this drift.
"""
from app.database import Profile, SessionLocal
from app.models import ProfileResponse


def test_profile_response_accepts_profile_row():
    """Insert a Profile, then build ProfileResponse from its column dict.

    Catches the PHA-342 class of bug where a column existed but the response
    constructor didn't pass it through (or the response field was missing).
    """
    db = SessionLocal()
    try:
        p = Profile(name="Schema Drift Probe")
        db.add(p)
        db.commit()
        db.refresh(p)
        row = {col.name: getattr(p, col.name) for col in Profile.__table__.columns}
        resp = ProfileResponse.model_validate(row)
        assert resp.name == "Schema Drift Probe"
        assert resp.entity_type == "person"
    finally:
        db.close()


def test_profile_response_fields_are_subset_of_profile_columns():
    """Every ProfileResponse field must map to a Profile column.

    Guard against the inverse drift: response declares a field the DB doesn't have.
    """
    profile_cols = {c.name for c in Profile.__table__.columns}
    response_fields = set(ProfileResponse.model_fields.keys())
    missing = response_fields - profile_cols
    assert not missing, f"ProfileResponse fields with no Profile column: {missing}"
