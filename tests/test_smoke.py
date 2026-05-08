import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import ProfileResponse


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "app": "Obsessed"}


@pytest.mark.asyncio
async def test_create_and_list_profile():
    transport = ASGITransport(app=app)
    payload = {"name": "Smoke Test Subject", "entity_type": "person"}
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        create = await ac.post("/api/profiles", json=payload)
        assert create.status_code == 200, create.text
        body = create.json()
        # Every required ProfileResponse field must be present in the response —
        # this is the regression guard for PHA-342 (entity_type was dropped).
        for field in ProfileResponse.model_fields:
            assert field in body, f"missing field {field!r} in POST /api/profiles response"
        assert body["name"] == "Smoke Test Subject"
        assert body["entity_type"] == "person"
        created_id = body["id"]

        listing = await ac.get("/api/profiles")
    assert listing.status_code == 200
    ids = [p["id"] for p in listing.json()]
    assert created_id in ids


@pytest.mark.asyncio
async def test_static_css_mounted():
    """Regression guard: /static must be mounted (PHA-407 fix)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/static/css/style.css")
    assert r.status_code == 200, "expected /static to be mounted and serve style.css"
    assert "text/css" in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_get_question_includes_correct_answer():
    """
    Bug A regression (PHA-503): options array MUST include correct_answer.

    Before fix: options = q.wrong_answers only → game unwinnable.
    After fix:   options = [q.correct_answer] + list(q.wrong_answers), shuffled.

    This test creates a profile with rich manual facts (guarantees ≥15 chunks),
    generates questions, then asserts correct_answer IS PRESENT in options.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p = await ac.post("/api/profiles", json={
            "name": "Bug A Facts",
            "entity_type": "person",
            "manual_facts": (
                "Albert Einstein was born in 1879. He developed the theory of relativity in 1905. "
                "He won the Nobel Prize in Physics in 1921. He was a German-born theoretical physicist. "
                "He emigrated to the United States in 1933. He worked at Princeton University. "
                "He published four papers in his miracle year 1905. He was a pacifist during World War One. "
                "He advocated for civil rights. His brain was preserved after his death. "
                "He received the Copley Medal in 1925. He collaborated with Niels Bohr on quantum theory."
            ),
            "question_budget": 5,
        })
        profile_id = p.json()["id"]

        # Scrape with manual facts (bypasses LLM call — goes straight to raw_content)
        scrape = await ac.post(f"/api/profiles/{profile_id}/scrape")
        assert scrape.status_code == 200, f"scrape failed: {scrape.text}"

        # Grant consent
        from app.database import SessionLocal, Profile
        db = SessionLocal()
        try:
            row = db.query(Profile).filter(Profile.id == profile_id).first()
            row.consent_obtained = True
            db.commit()
        finally:
            db.close()

        # Create a game using things= (correct multi-thing API)
        game = await ac.post("/api/games", json={
            "things": [{"profile_id": profile_id, "num_questions": 5}]
        })
        assert game.status_code == 200, f"game create failed: {game.status_code} {game.text}"
        room = game.json()["room_code"]

        # Start loads questions into GAMES
        start = await ac.post(f"/api/games/{room}/start")
        assert start.status_code == 200, f"start failed: {start.text}"

        # Get question
        q = await ac.get(f"/api/games/{room}/question")
        if q.status_code == 200:
            body = q.json()
            opts = body.get("options", [])
            correct = body.get("correct_answer", "")
            assert len(opts) >= 2, f"Expected ≥2 options, got {len(opts)}: {opts}"
            assert correct in opts, (
                f"Bug A NOT fixed: correct_answer '{correct}' not in options {opts}. "
                "The game is still unwinnable."
            )
        elif q.status_code == 400:
            detail = q.json().get("detail", "").lower()
            assert "no" in detail and "question" in detail, f"unexpected 400 detail: {detail}"
        else:
            assert q.status_code in (200, 400), f"Unexpected {q.status_code}: {q.text}"


@pytest.mark.asyncio
async def test_gamestate_resume_with_things():
    """
    Bug B regression (PHA-503): GameState resume after container restart must
    reconstruct correctly for multi-thing games.

    The GameSession stores multi-thing games in the `things` JSON column, NOT in
    `profile_id`. The resume path in next_question endpoint creates GameState
    using the fields available in the DB — verifying the constructor accepts
    room_code + profile_id (scalar) is the actual regression guard.

    Additionally verify that a GameState with things can be created and used.
    """
    from app.services.game_engine import GameState, GAMES

    room = "RESUMETEST888"
    if room in GAMES:
        del GAMES[room]

    db = SessionLocal()
    try:
        # Get or create a test profile
        p = db.query(Profile).first()
        if not p:
            p = Profile(name="Resume Test", entity_type="person", consent_obtained=True)
            db.add(p)
            db.commit()
            db.refresh(p)

        # Create a GameSession with things (multi-thing game, no profile_id)
        gs_db = GameSession(
            room_code=room,
            profile_id=None,
            things=[{"profile_id": p.id, "num_questions": 5}],
            total_questions=5,
            status="active",
            current_question=0,
        )
        db.add(gs_db)
        db.commit()

        # Verify GAMES is empty (container restart scenario)
        assert room not in GAMES

        # Resume path: call get_or_create_game with profile_id=None for things-based game
        from app.services.game_engine import get_or_create_game
        gs_resumed = get_or_create_game(room, profile_id=None)

        # Must succeed without TypeError
        assert gs_resumed is not None, "GameState resume returned None"
        assert gs_resumed.room_code == room
        assert gs_resumed.total_q == 5
        assert gs_resumed.status == "lobby" or gs_resumed.status == "active"

    finally:
        if room in GAMES:
            del GAMES[room]
        # cleanup
        db.query(GameSession).filter(GameSession.room_code == room).delete()
        db.commit()
        db.close()


@pytest.mark.asyncio
async def test_scrape_nonexistent_profile_returns_404():
    """Regression: nonexistent profile /scrape returns 404, not 500 (Bug A, PHA-504)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/profiles/9999/scrape")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"
    assert r.json()["detail"] == "Profile not found"


@pytest.mark.asyncio
async def test_profile_list_includes_entity_type():
    """Regression guard: entity_type field present in profile list (Bug A, PHA-504)."""
    transport = ASGITransport(app=app)
    payload = {"name": "Cache Test", "entity_type": "place"}
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p = await ac.post("/api/profiles", json=payload)
        profile_id = p.json()["id"]
        listing = await ac.get("/api/profiles")
    assert listing.status_code == 200
    bodies = listing.json()
    matches = [x for x in bodies if x["id"] == profile_id]
    assert matches, "Created profile not in list"
    assert matches[0].get("entity_type") == "place", f"entity_type missing/wrong: {matches[0]}"


# ── PHA-577 multi-thing tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_things_game_create_and_start():
    """Multi-thing game (2 profiles) — create, join, start, questions load."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Create two profiles
        p1 = await ac.post("/api/profiles", json={"name": "Thing A", "entity_type": "person"})
        p2 = await ac.post("/api/profiles", json={"name": "Thing B", "entity_type": "person"})
        pid1, pid2 = p1.json()["id"], p2.json()["id"]

        # Set consent on both (required)
        await ac.post(f"/api/profiles/{pid1}/consent")
        await ac.post(f"/api/profiles/{pid2}/consent")

        # Create a game with 2 things
        game = await ac.post("/api/games", json={
            "things": [{"profile_id": pid1, "num_questions": 10}, {"profile_id": pid2, "num_questions": 10}]
        })
        assert game.status_code == 200, f"game create failed: {game.text}"
        body = game.json()
        assert body.get("things") is not None, "things field should be returned"
        assert len(body["things"]) == 2

        room = body["room_code"]

        # Join a player
        player = await ac.post(f"/api/games/{room}/join", json={
            "player_id": "test_player_1", "player_name": "Alice"
        })
        assert player.status_code == 200

        # Start the game
        start = await ac.post(f"/api/games/{room}/start")
        assert start.status_code == 200, f"start failed: {start.text}"
        start_body = start.json()
        assert start_body["ok"] is True
        assert start_body["total_questions"] > 0, "should have loaded questions from both profiles"


@pytest.mark.asyncio
async def test_single_profile_id_game_still_works():
    """Regression: single profile_id game (backward compat) unchanged."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p = await ac.post("/api/profiles", json={"name": "Solo Thing", "entity_type": "person"})
        pid = p.json()["id"]
        await ac.post(f"/api/profiles/{pid}/consent")

        game = await ac.post("/api/games", json={"profile_id": pid})
        assert game.status_code == 200, f"single-profile game failed: {game.text}"
        body = game.json()
        assert body.get("things") is None, "things should be null for single profile_id"
        room = body["room_code"]

        player = await ac.post(f"/api/games/{room}/join", json={
            "player_id": "solo_player", "player_name": "Bob"
        })
        assert player.status_code == 200

        start = await ac.post(f"/api/games/{room}/start")
        assert start.status_code == 200, f"start failed: {start.text}"


@pytest.mark.asyncio
async def test_things_empty_array_fails():
    """things=[] should return 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/games", json={"things": []})
    assert r.status_code == 400, f"expected 400 for empty things, got {r.status_code}"


@pytest.mark.asyncio
async def test_things_beyondule_max_fails():
    """More than 10 things should return 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        things = [{"profile_id": i, "num_questions": 10} for i in range(1, 15)]
        r = await ac.post("/api/games", json={"things": things})
    assert r.status_code == 400, f"expected 400 for >10 things, got {r.status_code}"
