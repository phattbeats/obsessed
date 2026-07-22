"""
Tests for PHA-1337: narrow broad except in trigger_scrape and surface
per-source scrape errors.

Pre-fix behavior: trigger_scrape's outer ``except Exception`` swallowed every
error (including NameError, TypeError, AttributeError) and silently recorded
``scrape_status='failed'`` with the message body. That hid programming bugs
behind a status indistinguishable from real scraper outages.

Post-fix behavior:
- The outer except now catches ScraperError (502) separately from generic
  Exception (500). Programming errors are tagged with ``LogicError/<Type>:``
  in ``scrape_error`` so monitoring can split real bugs from real outages.
- Response carries a ``per_source`` dict showing which scrapers succeeded vs
  failed vs returned empty (so callers don't have to grok a string blob).
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.scraper import ScraperError
from app.database import SessionLocal, Profile


@pytest.mark.asyncio
async def test_trigger_scrape_returns_per_source_dict():
    """A successful scrape must include a per_source dict in the response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        create = await ac.post("/api/profiles", json={
            "name": "PHA-1337 PerSource Subject",
            "entity_type": "person",
            "wikipedia_handle": "Python_(programming_language)",  # real wiki page
        })
        assert create.status_code == 200, create.text
        pid = create.json()["id"]

        result = await ac.post(f"/api/profiles/{pid}/scrape")
        assert result.status_code == 200, result.text
        body = result.json()
        assert "per_source" in body, "per_source dict missing from response"
        assert isinstance(body["per_source"], dict)
        # Wikipedia was a configured source — it must be in per_source.
        assert "Wikipedia" in body["per_source"]
        assert body["per_source"]["Wikipedia"] in {"ok", "empty"}, (
            f"unexpected per_source value: {body['per_source']['Wikipedia']!r}"
        )
        # Sources we did NOT configure must not appear in per_source.
        assert "Reddit" not in body["per_source"]


@pytest.mark.asyncio
async def test_trigger_scrape_scraper_error_returns_502_narrowly(monkeypatch):
    """A ScraperError outside _safe() must produce HTTP 502, not 500."""
    from app.services.scraper import osm as osm_mod

    async def _raise_scraper_error(*args, **kwargs):
        raise ScraperError("osm endpoint returned 503")

    # Profile has an osm_query so the inline `await search_osm(...)` call
    # (outside _safe()) executes during trigger_scrape.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        create = await ac.post("/api/profiles", json={
            "name": "PHA-1337 ScraperError Subject",
            "entity_type": "place",
            "osm_query": "Columbus, Ohio",
        })
        assert create.status_code == 200, create.text
        pid = create.json()["id"]

        monkeypatch.setattr(osm_mod, "search_osm", _raise_scraper_error)

        result = await ac.post(f"/api/profiles/{pid}/scrape")

    assert result.status_code == 502, (
        f"expected 502 for ScraperError, got {result.status_code}: {result.text}"
    )
    detail = result.json().get("detail", "")
    assert "ScraperError" in detail, f"missing error class in 502 detail: {detail!r}"
    assert "osm endpoint returned 503" in detail, (
        f"missing original message in 502 detail: {detail!r}"
    )

    # DB should reflect the failure with the ScraperError: prefix.
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == pid).first()
        assert p.scrape_status == "failed"
        assert p.scrape_error.startswith("ScraperError:"), (
            f"expected ScraperError: prefix, got {p.scrape_error!r}"
        )
        assert "ScraperError" not in p.scrape_error.split(":", 1)[0].replace(
            "LogicError", ""
        ) or p.scrape_error.startswith("ScraperError:")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_trigger_scrape_logic_error_no_longer_silent(monkeypatch):
    """A NameError in scraper code must surface with type name, not be hidden
    under a generic 'failed' string."""
    from app.services.scraper import osm as osm_mod

    async def _raise_name_error(*args, **kwargs):
        # Simulate the kind of bug the issue describes — a real NameError
        # from a typo or refactor that was being silently swallowed.
        return typo_variable_exists_only_in_my_imagination  # noqa: F821

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        create = await ac.post("/api/profiles", json={
            "name": "PHA-1337 LogicError Subject",
            "entity_type": "place",
            "osm_query": "Columbus, Ohio",
        })
        assert create.status_code == 200, create.text
        pid = create.json()["id"]

        monkeypatch.setattr(osm_mod, "search_osm", _raise_name_error)

        result = await ac.post(f"/api/profiles/{pid}/scrape")

    # Pre-fix this was 500 with detail=str(NameError()) — indistinguishable
    # from any other 500. Post-fix it must (a) include the error class name
    # in the response body and (b) tag DB scrape_error with LogicError/<Type>
    # so Sentry-style monitors can split logic bugs from scraper outages.
    assert result.status_code == 500
    detail = result.json().get("detail", "")
    assert "NameError" in detail, (
        f"NameError class missing from 500 detail; got: {detail!r}"
    )
    assert "typo_variable" in detail, (
        f"original NameError message not preserved: {detail!r}"
    )

    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == pid).first()
        assert p.scrape_status == "failed"
        assert p.scrape_error.startswith("LogicError/NameError:"), (
            f"expected LogicError/NameError: prefix, got {p.scrape_error!r}"
        )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_trigger_scrape_safe_wrapper_records_per_source_failure(monkeypatch):
    """A scraper raising inside _safe() must appear in per_source as 'error: ...'."""
    from app.services.scraper import wikipedia as wiki_mod

    async def _boom(handle):
        raise RuntimeError("wiki returned 404")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        create = await ac.post("/api/profiles", json={
            "name": "PHA-1337 SafeFailure Subject",
            "entity_type": "person",
            "wikipedia_handle": "Some_Page",
        })
        assert create.status_code == 200, create.text
        pid = create.json()["id"]

        monkeypatch.setattr(wiki_mod, "scrape_wikipedia", _boom)

        result = await ac.post(f"/api/profiles/{pid}/scrape")
        body = result.json()

    assert result.status_code == 200
    assert "Wikipedia" in body["per_source"], (
        f"Wikipedia missing from per_source: {body['per_source']}"
    )
    assert body["per_source"]["Wikipedia"].startswith("error:"), (
        f"expected 'error:' prefix, got {body['per_source']['Wikipedia']!r}"
    )
    assert "wiki returned 404" in body["per_source"]["Wikipedia"]
    # And the legacy list-form is still populated.
    assert any("Wikipedia:" in e for e in body["scraper_errors"])
