"""Tests for the FamilySearch Family Tree client (PHA-1349).

httpx is patched at module level (same pattern as test_captcha_solver.py) so
these exercise the token/search/relatives flow without hitting the real API.
GEDCOM X fixtures below are hand-built to FamilySearch's documented
Conclusion-format shape (the `display` extension + relationship resourceId
refs) since no client ID was available to capture a live response this
session — see the module docstring in familysearch.py.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scraper import familysearch
from app.services.scraper.familysearch import (
    FamilySearchError,
    FamilySearchNotConfigured,
    get_person,
    get_relatives,
    is_configured,
    parse_person,
    parse_relatives,
    parse_search_results,
    search_person,
)


@pytest.fixture(autouse=True)
def _reset_token_cache():
    familysearch.reset_token_cache()
    yield
    familysearch.reset_token_cache()


def _build_mock_client(responses: list[tuple[int, dict]]):
    """Fake httpx.AsyncClient whose .get()/.post() walk `responses` in order.

    Each entry is (status_code, json_body).
    """
    iter_responses = iter(responses)

    async def fake_request(*args, **kwargs):
        status, body = next(iter_responses)
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = body
        resp.text = str(body)
        return resp

    client_instance = MagicMock()
    client_instance.get = AsyncMock(side_effect=fake_request)
    client_instance.post = AsyncMock(side_effect=fake_request)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=client_instance)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=async_cm)
    return factory, client_instance


# --- Configuration gating --------------------------------------------------


def test_is_configured_false_when_client_id_empty():
    with patch.object(familysearch.settings, "familysearch_client_id", ""):
        assert is_configured() is False


def test_is_configured_true_when_client_id_set():
    with patch.object(familysearch.settings, "familysearch_client_id", "abc123"):
        assert is_configured() is True


def test_search_person_returns_wall_when_not_configured():
    with patch.object(familysearch.settings, "familysearch_client_id", ""):
        result = asyncio.run(search_person(given_name="John", surname="Smith"))
    assert result["results"] == []
    assert result["wall"]["kind"] == "familysearch_not_configured"


def test_get_person_returns_wall_when_not_configured():
    with patch.object(familysearch.settings, "familysearch_client_id", ""):
        result = asyncio.run(get_person("PERSON-1"))
    assert result["person"] is None
    assert result["wall"]["kind"] == "familysearch_not_configured"


def test_get_relatives_returns_wall_when_not_configured():
    with patch.object(familysearch.settings, "familysearch_client_id", ""):
        result = asyncio.run(get_relatives("PERSON-1"))
    assert result["parents"] == result["spouses"] == result["children"] == []
    assert result["wall"]["kind"] == "familysearch_not_configured"


# --- Token acquisition + caching -------------------------------------------


def test_get_token_fetches_and_caches():
    factory, client = _build_mock_client([
        (200, {"access_token": "TOKEN-1", "expires_in": 3600}),
    ])
    with patch.object(familysearch.settings, "familysearch_client_id", "client-abc"), \
         patch("app.services.scraper.familysearch.httpx.AsyncClient", factory):
        token1 = asyncio.run(familysearch._get_token())
        token2 = asyncio.run(familysearch._get_token())  # should hit the cache

    assert token1 == token2 == "TOKEN-1"
    assert client.post.await_count == 1  # only one token request made


def test_get_token_raises_on_non_200():
    factory, _client = _build_mock_client([
        (400, {"error": "invalid_grant", "error_description": "Client not found."}),
    ])
    with patch.object(familysearch.settings, "familysearch_client_id", "bad-client"), \
         patch("app.services.scraper.familysearch.httpx.AsyncClient", factory):
        with pytest.raises(FamilySearchError):
            asyncio.run(familysearch._get_token())


def test_search_person_raises_not_configured_without_client_id():
    with patch.object(familysearch.settings, "familysearch_client_id", ""):
        with pytest.raises(FamilySearchNotConfigured):
            asyncio.run(familysearch._get_token())


# --- GEDCOM X parsing -------------------------------------------------------


_PERSON_WITH_DISPLAY = {
    "id": "ABCD-123",
    "living": False,
    "display": {
        "name": "John Smith",
        "gender": "Male",
        "lifespan": "1939-2026",
        "birthDate": "5 May 1939",
        "birthPlace": "Ohio, United States",
        "deathDate": "1 January 2026",
        "deathPlace": "Columbus, Ohio, United States",
    },
}

_PERSON_RAW_ONLY = {
    "id": "WXYZ-456",
    "gender": {"type": "http://gedcomx.org/Male"},
    "names": [{"nameForms": [{"fullText": "Jane Doe"}]}],
    "facts": [
        {"type": "http://gedcomx.org/Birth", "date": {"original": "1910"}, "place": {"original": "Ohio"}},
        {"type": "http://gedcomx.org/Death", "date": {"original": "1985"}, "place": {"original": "Ohio"}},
    ],
}


def test_parse_person_prefers_display_extension():
    parsed = parse_person(_PERSON_WITH_DISPLAY)
    assert parsed == {
        "id": "ABCD-123",
        "name": "John Smith",
        "gender": "Male",
        "birth_date": "5 May 1939",
        "birth_place": "Ohio, United States",
        "death_date": "1 January 2026",
        "death_place": "Columbus, Ohio, United States",
        "living": False,
    }


def test_parse_person_falls_back_to_raw_names_and_facts():
    parsed = parse_person(_PERSON_RAW_ONLY)
    assert parsed["name"] == "Jane Doe"
    assert parsed["gender"] == "Male"
    assert parsed["birth_date"] == "1910"
    assert parsed["death_date"] == "1985"


def test_parse_search_results_extracts_persons_and_score():
    doc = {
        "entries": [
            {"score": 0.92, "content": {"gedcomx": {"persons": [_PERSON_WITH_DISPLAY]}}},
            {"score": 0.4, "content": {"gedcomx": {"persons": []}}},  # no person -> skipped
        ]
    }
    results = parse_search_results(doc)
    assert len(results) == 1
    assert results[0]["name"] == "John Smith"
    assert results[0]["match_score"] == 0.92


def test_parse_relatives_extracts_other_side_of_relationship():
    doc = {
        "persons": [_PERSON_WITH_DISPLAY, _PERSON_RAW_ONLY],
        "relationships": [
            {
                "type": "http://gedcomx.org/ParentChild",
                "person1": {"resourceId": "WXYZ-456"},
                "person2": {"resourceId": "ABCD-123"},
            }
        ],
    }
    parents = parse_relatives(doc, "ABCD-123", "ParentChild")
    assert len(parents) == 1
    assert parents[0]["id"] == "WXYZ-456"
    assert parents[0]["name"] == "Jane Doe"


def test_parse_relatives_ignores_other_relationship_types():
    doc = {
        "persons": [_PERSON_WITH_DISPLAY, _PERSON_RAW_ONLY],
        "relationships": [
            {
                "type": "http://gedcomx.org/Couple",
                "person1": {"resourceId": "WXYZ-456"},
                "person2": {"resourceId": "ABCD-123"},
            }
        ],
    }
    assert parse_relatives(doc, "ABCD-123", "ParentChild") == []


# --- End-to-end (token + API calls) ----------------------------------------


def test_search_person_end_to_end():
    factory, _client = _build_mock_client([
        (200, {"access_token": "TOKEN-1", "expires_in": 3600}),  # token
        (200, {"entries": [{"score": 0.9, "content": {"gedcomx": {"persons": [_PERSON_WITH_DISPLAY]}}}]}),  # search
    ])
    with patch.object(familysearch.settings, "familysearch_client_id", "client-abc"), \
         patch("app.services.scraper.familysearch.httpx.AsyncClient", factory):
        result = asyncio.run(search_person(given_name="John", surname="Smith"))

    assert result["wall"] is None
    assert result["results"][0]["name"] == "John Smith"


def test_get_relatives_end_to_end():
    parents_doc = (200, {
        "persons": [_PERSON_RAW_ONLY],
        "relationships": [{
            "type": "http://gedcomx.org/ParentChild",
            "person1": {"resourceId": "WXYZ-456"},
            "person2": {"resourceId": "ABCD-123"},
        }],
    })
    spouses_doc = (200, {"persons": [], "relationships": []})
    children_doc = (200, {"persons": [], "relationships": []})
    factory, _client = _build_mock_client([
        (200, {"access_token": "TOKEN-1", "expires_in": 3600}),
        parents_doc,
        spouses_doc,
        children_doc,
    ])
    with patch.object(familysearch.settings, "familysearch_client_id", "client-abc"), \
         patch("app.services.scraper.familysearch.httpx.AsyncClient", factory):
        result = asyncio.run(get_relatives("ABCD-123"))

    assert result["wall"] is None
    assert len(result["parents"]) == 1
    assert result["parents"][0]["name"] == "Jane Doe"
    assert result["spouses"] == []
    assert result["children"] == []
