"""
Regression tests for PHA-1564: GET /api/games/{room}/scores 404s for REST-created games.

The /scores endpoint originally read only the in-memory GAMES dict, which is
populated via the WebSocket path. Games created via POST /api/games enter the
dict at create time, but the entry is removed by cleanup_game() when the game
ends and is absent after any container restart — yet the DB row persists.

This file pins the contract: /scores must work for any room that exists in the
DB, regardless of whether the in-memory GAMES dict still has it.
"""
import json
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.database import SessionLocal, Profile, GameSession, Player
from app.services.game_engine import GAMES, cleanup_game


async def _create_profile_with_consent(ac: AsyncClient, name: str) -> int:
    """Helper: create a profile and grant consent for game creation."""
    p = await ac.post("/api/profiles", json={
        "name": name,
        "entity_type": "person",
    })
    assert p.status_code == 200, p.text
    profile_id = p.json()["id"]

    db = SessionLocal()
    try:
        row = db.query(Profile).filter(Profile.id == profile_id).first()
        row.consent_obtained = True
        db.commit()
    finally:
        db.close()
    return profile_id


@pytest.mark.asyncio
async def test_scores_works_while_in_memory_dict_has_game():
    """Baseline: /scores returns player data when the in-memory GAMES dict has the room."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        profile_id = await _create_profile_with_consent(ac, "PHA-1564 in-mem")

        game = await ac.post("/api/games", json={"profile_id": profile_id})
        assert game.status_code == 200, game.text
        room = game.json()["room_code"]

        join = await ac.post(f"/api/games/{room}/join", json={
            "player_id": "p_alpha", "player_name": "Alpha",
        })
        assert join.status_code == 200, join.text

        # Sanity: game should be in the in-memory dict right after create + join
        assert room in GAMES, "GAMES dict should have the room right after create+join"

        r = await ac.get(f"/api/games/{room}/scores")
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert isinstance(body, list), f"expected list, got {type(body)}"
        assert len(body) == 1, f"expected 1 player, got {len(body)}"
        assert body[0]["player_id"] == "p_alpha"
        assert body[0]["player_name"] == "Alpha"
        assert body[0]["score"] == 0
        assert body[0]["wedges"] == []


@pytest.mark.asyncio
async def test_scores_falls_back_to_db_after_cleanup_game():
    """PHA-1564: /scores must still work after the room is removed from the in-memory dict.

    Simulates the post-game state: cleanup_game() is called when a game ends,
    which removes the entry from GAMES. /scores must fall back to the DB row.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        profile_id = await _create_profile_with_consent(ac, "PHA-1564 post-cleanup")

        game = await ac.post("/api/games", json={"profile_id": profile_id})
        assert game.status_code == 200, game.text
        room = game.json()["room_code"]

        join = await ac.post(f"/api/games/{room}/join", json={
            "player_id": "p_bravo", "player_name": "Bravo",
        })
        assert join.status_code == 200, join.text

        # Score the player so the DB row has non-zero values (proves DB fallback
        # isn't returning a default-zero fabricated score).
        db = SessionLocal()
        try:
            gs_row = db.query(GameSession).filter(GameSession.room_code == room).first()
            player_row = db.query(Player).filter(
                Player.game_id == gs_row.id, Player.player_id == "p_bravo"
            ).first()
            player_row.score = 1750
            player_row.wedges = json.dumps(["history", "geography"])
            db.commit()
        finally:
            db.close()

        # Simulate the room leaving the in-memory dict (game ended, container
        # restarted, or any other path that removes it from GAMES).
        cleanup_game(room)
        assert room not in GAMES, "cleanup_game should remove the room from GAMES"

        # Confirm the DB row still exists — the room is not actually gone
        db = SessionLocal()
        try:
            gs_row = db.query(GameSession).filter(GameSession.room_code == room).first()
            assert gs_row is not None, "DB row should still exist"
            assert len(gs_row.players) == 1, "DB row should still have the player"
        finally:
            db.close()

        # /scores must fall back to the DB and return the player
        r = await ac.get(f"/api/games/{room}/scores")
        assert r.status_code == 200, (
            f"expected 200 with DB fallback, got {r.status_code}: {r.text} — "
            "this is the PHA-1564 bug"
        )
        body = r.json()
        assert isinstance(body, list), f"expected list, got {type(body)}"
        assert len(body) == 1, f"expected 1 player from DB, got {len(body)}"
        assert body[0]["player_id"] == "p_bravo"
        assert body[0]["player_name"] == "Bravo"
        assert body[0]["score"] == 1750, (
            f"score should come from DB (1750), got {body[0]['score']}"
        )
        assert sorted(body[0]["wedges"]) == ["geography", "history"], (
            f"wedges should come from DB, got {body[0]['wedges']}"
        )


@pytest.mark.asyncio
async def test_scores_404s_for_nonexistent_room():
    """Sanity: rooms that don't exist in DB OR GAMES still 404."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/games/ZZZZZZ/scores")
    assert r.status_code == 404, f"expected 404 for nonexistent room, got {r.status_code}"
    assert r.json()["detail"] == "Game not found"


@pytest.mark.asyncio
async def test_scores_excludes_inactive_players_in_db_fallback():
    """DB-fallback path must honor is_active=False (matches in-memory get_scores contract)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        profile_id = await _create_profile_with_consent(ac, "PHA-1564 inactive")

        game = await ac.post("/api/games", json={"profile_id": profile_id})
        assert game.status_code == 200, game.text
        room = game.json()["room_code"]

        # Two players
        for pid, name in [("p_charlie", "Charlie"), ("p_delta", "Delta")]:
            await ac.post(f"/api/games/{room}/join", json={
                "player_id": pid, "player_name": name,
            })

        # Mark Delta as inactive in DB
        db = SessionLocal()
        try:
            gs_row = db.query(GameSession).filter(GameSession.room_code == room).first()
            player_row = db.query(Player).filter(
                Player.game_id == gs_row.id, Player.player_id == "p_delta"
            ).first()
            player_row.is_active = False
            db.commit()
        finally:
            db.close()

        # Trigger DB fallback path
        cleanup_game(room)

        r = await ac.get(f"/api/games/{room}/scores")
        assert r.status_code == 200, r.text
        body = r.json()
        names = sorted(p["player_name"] for p in body)
        assert names == ["Charlie"], (
            f"inactive player Delta should be filtered out, got {names}"
        )


@pytest.mark.asyncio
async def test_scores_db_fallback_sorts_by_score_descending():
    """DB-fallback must match the in-memory sort order (score desc)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        profile_id = await _create_profile_with_consent(ac, "PHA-1564 sort")

        game = await ac.post("/api/games", json={"profile_id": profile_id})
        assert game.status_code == 200, game.text
        room = game.json()["room_code"]

        for pid, name in [("p_echo", "Echo"), ("p_foxtrot", "Foxtrot"), ("p_golf", "Golf")]:
            await ac.post(f"/api/games/{room}/join", json={
                "player_id": pid, "player_name": name,
            })

        db = SessionLocal()
        try:
            gs_row = db.query(GameSession).filter(GameSession.room_code == room).first()
            scores_map = {"p_echo": 500, "p_foxtrot": 2200, "p_golf": 1100}
            for p_row in gs_row.players:
                p_row.score = scores_map.get(p_row.player_id, 0)
            db.commit()
        finally:
            db.close()

        cleanup_game(room)  # force DB fallback path

        r = await ac.get(f"/api/games/{room}/scores")
        assert r.status_code == 200, r.text
        body = r.json()
        order = [(p["player_name"], p["score"]) for p in body]
        assert order == [("Foxtrot", 2200), ("Golf", 1100), ("Echo", 500)], (
            f"scores should sort descending by score, got {order}"
        )