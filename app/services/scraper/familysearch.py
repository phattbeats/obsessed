"""
FamilySearch Family Tree API client (PHA-1349) — genealogy/relationship facts.

FamilySearch has no key-less public endpoint: every call, including read-only
browsing, requires a registered developer app. Register a free one at
https://www.familysearch.org/developers/ to get a client ID, then use the
"unauthenticated session" OAuth2 grant (`grant_type=unauthenticated_session`)
to get a token with no end-user login — this is FamilySearch's documented
path for a server reading the public, collaborative Family Tree rather than
editing a signed-in user's own tree.

Confirmed live 2026-07-11 (this container, no client ID configured): both
`ident.familysearch.org` and `identbeta.familysearch.org` are reachable and
respond `400 {"error":"invalid_grant","error_description":"Client not found."}`
for the unauthenticated_session grant — i.e. the auth flow and hosts below are
live and correct, but a real client ID is required past that point. The
Family Tree person-search/read/relatives response parsing that follows is
built to FamilySearch's documented GEDCOM X Conclusion format (the `display`
extension + dedicated /parents /children /spouses relative endpoints) but
has NOT been exercised against a live token — there was no client ID
available to register during this session. Spot-check `parse_person` and
`parse_relatives` against a real response once FAMILYSEARCH_CLIENT_ID is set;
adjust field paths there if FamilySearch's actual shape drifts from the docs.

Standalone-safe by design: FAMILYSEARCH_CLIENT_ID unset (the default) means
every public function returns a typed `familysearch_not_configured` wall and
makes zero network calls.
"""

from __future__ import annotations

import time
from typing import Optional

import httpx

from app.config import settings


_IDENT_TOKEN_URL = "https://ident.familysearch.org/cis-web/oauth2/v3/token"
_API_BASE = "https://api.familysearch.org"
_REQUEST_TIMEOUT = 30.0
_TOKEN_TTL = 3300  # unauthenticated sessions are documented as ~1h; refresh a bit early

_token_cache: Optional[tuple[str, float]] = None


class FamilySearchError(Exception):
    """Base for FamilySearch API failures."""


class FamilySearchNotConfigured(FamilySearchError):
    """FAMILYSEARCH_CLIENT_ID is unset."""


def is_configured() -> bool:
    return bool((settings.familysearch_client_id or "").strip())


def reset_token_cache() -> None:
    """Clear the cached session token (used by tests)."""
    global _token_cache
    _token_cache = None


def wall_for(exc: Exception) -> Optional[dict]:
    """Map a FamilySearch exception to a typed wall dict, or None if it's not one."""
    if isinstance(exc, FamilySearchNotConfigured):
        return {"kind": "familysearch_not_configured", "detail": str(exc)}
    if isinstance(exc, FamilySearchError):
        return {"kind": "familysearch_error", "detail": str(exc)}
    return None


async def _get_token() -> str:
    """Return a cached or freshly-obtained unauthenticated-session access token."""
    global _token_cache
    if _token_cache is not None:
        token, expires_at = _token_cache
        if time.monotonic() < expires_at:
            return token

    client_id = (settings.familysearch_client_id or "").strip()
    if not client_id:
        raise FamilySearchNotConfigured(
            "FAMILYSEARCH_CLIENT_ID is not configured. Register a free app at "
            "https://www.familysearch.org/developers/ to get one."
        )

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(
            _IDENT_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={"grant_type": "unauthenticated_session", "client_id": client_id},
        )
    if resp.status_code != 200:
        raise FamilySearchError(
            f"FamilySearch token request failed: {resp.status_code} {resp.text[:200]}"
        )
    token = resp.json().get("access_token")
    if not token:
        raise FamilySearchError("FamilySearch token response had no access_token")

    _token_cache = (token, time.monotonic() + _TOKEN_TTL)
    return token


async def _api_get(path: str, params: Optional[dict] = None) -> dict:
    token = await _get_token()
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.get(
            _API_BASE + path,
            params=params or {},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/x-gedcomx-v1+json",
            },
        )
    if resp.status_code == 401:
        # Token may have been invalidated server-side; drop the cache and let
        # the caller retry rather than silently looping here.
        reset_token_cache()
        raise FamilySearchError(f"FamilySearch request unauthorized: {path}")
    if resp.status_code != 200:
        raise FamilySearchError(
            f"FamilySearch request failed: {path} -> {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


# ---------------------------------------------------------------------------
# GEDCOM X parsing
# ---------------------------------------------------------------------------
#
# FamilySearch's Family Tree responses are GEDCOM X Conclusion documents. Each
# `Person` carries a convenience `display` extension
# (name/lifespan/birthDate/birthPlace/deathDate/deathPlace/gender) alongside
# the raw `names`/`facts`/`gender` structures; prefer `display` when present
# and fall back to the raw fields otherwise.

def _fact(person: dict, fact_type: str) -> Optional[dict]:
    for f in person.get("facts") or []:
        if f.get("type") == f"http://gedcomx.org/{fact_type}":
            return f
    return None


def _fact_date(person: dict, fact_type: str) -> Optional[str]:
    f = _fact(person, fact_type)
    return (f or {}).get("date", {}).get("original")


def _fact_place(person: dict, fact_type: str) -> Optional[str]:
    f = _fact(person, fact_type)
    return (f or {}).get("place", {}).get("original")


def parse_person(person: dict) -> dict:
    """Normalize a GEDCOM X Person to {id, name, gender, birth_date,
    birth_place, death_date, death_place, living}."""
    display = person.get("display") or {}
    name = display.get("name")
    if not name:
        names = person.get("names") or []
        if names:
            name = (names[0].get("nameForms") or [{}])[0].get("fullText")

    return {
        "id": person.get("id"),
        "name": name,
        "gender": display.get("gender") or (person.get("gender") or {}).get("type", "").rsplit("/", 1)[-1] or None,
        "birth_date": display.get("birthDate") or _fact_date(person, "Birth"),
        "birth_place": display.get("birthPlace") or _fact_place(person, "Birth"),
        "death_date": display.get("deathDate") or _fact_date(person, "Death"),
        "death_place": display.get("deathPlace") or _fact_place(person, "Death"),
        "living": person.get("living"),
    }


def parse_search_results(doc: dict) -> list[dict]:
    """Normalize a Person Search response's `entries` to person dicts.

    Each Atom entry wraps a GEDCOM X document at
    `entry.content.gedcomx.persons[0]`; `entry.score` (search relevance) is
    carried through as `match_score`.
    """
    results = []
    for entry in doc.get("entries") or []:
        gx = ((entry.get("content") or {}).get("gedcomx")) or {}
        persons = gx.get("persons") or []
        if not persons:
            continue
        parsed = parse_person(persons[0])
        parsed["match_score"] = entry.get("score")
        results.append(parsed)
    return results


def parse_relatives(doc: dict, subject_id: str, relationship_type: str) -> list[dict]:
    """Normalize a /parents, /children, or /spouses response.

    These return `{"persons": [...], "relationships": [...]}`; each
    relationship names `person1`/`person2` by resourceId (a `#<id>` fragment).
    Returns every person present who isn't the subject themself.
    """
    persons_by_id = {p["id"]: p for p in doc.get("persons") or []}
    relation_people = []
    seen: set[str] = set()
    for rel in doc.get("relationships") or []:
        if rel.get("type") != f"http://gedcomx.org/{relationship_type}":
            continue
        for side in ("person1", "person2"):
            ref = (rel.get(side) or {}).get("resourceId")
            if not ref or ref == subject_id or ref in seen:
                continue
            person = persons_by_id.get(ref)
            if person is None:
                continue
            seen.add(ref)
            relation_people.append(parse_person(person))
    return relation_people


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_person(
    given_name: Optional[str] = None,
    surname: Optional[str] = None,
    birth_year: Optional[int] = None,
    death_year: Optional[int] = None,
) -> dict:
    """Search the Family Tree by name (and optional birth/death year).

    Returns::

        {"results": list[dict], "wall": None | dict}

    Wall kinds: familysearch_not_configured, familysearch_error.
    """
    params = {}
    if given_name:
        params["q.givenName"] = given_name
    if surname:
        params["q.surname"] = surname
    if birth_year:
        params["q.birthLikeDate"] = str(birth_year)
    if death_year:
        params["q.deathLikeDate"] = str(death_year)

    try:
        doc = await _api_get("/platform/tree/search", params)
    except FamilySearchError as exc:
        return {"results": [], "wall": wall_for(exc)}
    return {"results": parse_search_results(doc), "wall": None}


async def get_person(person_id: str) -> dict:
    """Fetch a single Family Tree person by ID.

    Returns::

        {"person": dict | None, "wall": None | dict}
    """
    try:
        doc = await _api_get(f"/platform/tree/persons/{person_id}")
    except FamilySearchError as exc:
        return {"person": None, "wall": wall_for(exc)}
    persons = doc.get("persons") or []
    return {"person": parse_person(persons[0]) if persons else None, "wall": None}


async def get_relatives(person_id: str) -> dict:
    """Fetch a person's parents, spouses, and children — the relationship
    facts genealogy lookups need.

    Returns::

        {
          "parents": list[dict],
          "spouses": list[dict],
          "children": list[dict],
          "wall": None | dict,
        }

    A wall on any one call short-circuits the rest (they share the same
    token/config failure mode).
    """
    empty = {"parents": [], "spouses": [], "children": [], "wall": None}
    try:
        parents_doc = await _api_get(f"/platform/tree/persons/{person_id}/parents")
        spouses_doc = await _api_get(f"/platform/tree/persons/{person_id}/spouses")
        children_doc = await _api_get(f"/platform/tree/persons/{person_id}/children")
    except FamilySearchError as exc:
        return {**empty, "wall": wall_for(exc)}

    return {
        "parents": parse_relatives(parents_doc, person_id, "ParentChild"),
        "spouses": parse_relatives(spouses_doc, person_id, "Couple"),
        "children": parse_relatives(children_doc, person_id, "ParentChild"),
        "wall": None,
    }
