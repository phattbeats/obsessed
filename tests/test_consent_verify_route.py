"""
Regression guard for PHA-1539.

The route previously declared at ``@router.get("/profiles/consent/verify")``
resolved (with the ``/api/profiles`` router prefix) to the malformed
``GET /api/profiles/profiles/consent/verify`` path. The correct public URL —
``GET /api/profiles/consent/verify`` — must be registered.

We probe three behaviors that together pin the route down:

1. ``GET /api/profiles/consent/verify`` (no token) → FastAPI 422 with a
   ``"missing"`` error on the ``token`` query parameter. This proves the
   route is registered *and* that ``token`` is a required query parameter.

2. ``GET /api/profiles/consent/verify?token=definitely-not-real`` → handler
   404 with the specific detail ``"Invalid consent link"``. Only the
   consent handler emits that body.

3. ``GET /api/profiles/profiles/consent/verify`` (the broken duplicate
   segment) → must NOT emit the consent handler detail. Either FastAPI's
   auto-404 or a method-not-allowed is fine; what must hold is that the
   malformed URL no longer routes to ``verify_consent``.

If a future change reintroduces the duplicate ``profiles`` segment (or
moves the route away from the documented URL), at least one of these
probes will fail and the bug cannot return silently.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


CONSENT_BROKEN_PATH = "/api/profiles/profiles/consent/verify"
CONSENT_CORRECT_PATH = "/api/profiles/consent/verify"


@pytest.mark.asyncio
async def test_correct_path_requires_token_query_param():
    """``GET /api/profiles/consent/verify`` (no token) → FastAPI 422.

    This proves the route is registered *and* that ``token`` is a required
    query parameter — matching the OpenAPI spec and the existing client
    contract. If the route is unregistered FastAPI returns 404 here, not
    422, so this single probe locks down both halves.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get(CONSENT_CORRECT_PATH)
    assert r.status_code == 422, (
        f"expected 422 (missing token) on {CONSENT_CORRECT_PATH}, got "
        f"{r.status_code}: {r.text!r}"
    )
    body = r.json()
    errs = body.get("detail", [])
    assert any(
        e.get("loc") == ["query", "token"] and e.get("type") == "missing"
        for e in errs
    ), f"expected missing-token validation error, got {body!r}"


@pytest.mark.asyncio
async def test_correct_path_invokes_handler():
    """``GET /api/profiles/consent/verify?token=...`` → consent handler 404.

    With an unknown token the handler returns
    ``HTTPException(404, "Invalid consent link")``. The exact detail string
    is the smoking gun — only ``verify_consent`` emits that body.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get(CONSENT_CORRECT_PATH, params={"token": "no-such-token-xyz"})
    assert r.status_code == 404, (
        f"expected 404 from consent handler, got {r.status_code}: {r.text!r}"
    )
    assert r.json() == {"detail": "Invalid consent link"}, (
        f"unexpected body — does not look like the consent handler: "
        f"{r.json()!r}"
    )


@pytest.mark.asyncio
async def test_broken_path_does_not_route_to_handler():
    """The malformed duplicate-segment URL must NOT resolve to the handler.

    Either FastAPI auto-404s (``{"detail": "Not Found"}``) or a method guard
    rejects it — both prove the consent handler no longer lives at this URL.
    Critically the bad path MUST NOT emit ``"Invalid consent link"``, because
    that string only comes from ``verify_consent``.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get(CONSENT_BROKEN_PATH, params={"token": "no-such-token-xyz"})
    body = r.json()
    assert body.get("detail") != "Invalid consent link", (
        "the consent handler is still mounted at the malformed path "
        f"{CONSENT_BROKEN_PATH!r} (response: {r.status_code} {body!r})"
    )
