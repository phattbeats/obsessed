import random, json
from fastapi import APIRouter, HTTPException, BackgroundTasks
import time as _time_module
from app.database import SessionLocal, Profile, Question, GameSession, Player, Answer, PlayerStats
from app.models import (
    GameCreate, GameResponse, PlayerJoin, PlayerResponse,
    AnswerSubmit, AnswerResponse, QuestionDisplay,
)
from app.routes.profiles import trigger_scrape
from app.services.game_engine import (
    GAMES, GameState, TriviaQuestion, PlayerState,
    get_or_create_game, _get_or_create_game_locked, get_room_lock, generate_room_code, cleanup_game,
)
from app.websocket import broadcast, send_to
from datetime import datetime, timezone

router = APIRouter(prefix="/api/games", tags=["games"])

CATEGORY_COLORS = {
    "history": "#ff6d00",
    "entertainment": "#d500f9",
    "geography": "#2979ff",
    "science": "#00e676",
    "sports": "#ff1744",
    "art_literature": "#ffea00",
}

def _load_game_to_memory(room_code: str):
    """Sync a game from SQLite into in-memory GameState.

    MUST be called under the room lock (caller holds it). Mutates GameState
    in place; the lock ensures no concurrent mutation interleaves.
    """
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            return
        gs = GAMES.get(room_code)
        if gs is None:
            # Construct a fresh GameState directly under the lock; we
            # can't await get_or_create_game() here because this helper is
            # sync. The lock guards the create-or-get decision.
            gs = GameState(room_code=room_code, profile_id=g.profile_id)
            GAMES[room_code] = gs
        gs.status = g.status
        gs.current_q = g.current_question
        gs.total_q = g.total_questions  # restore from DB, not constructor default
        # Load players
        for p in g.players:
            if p.player_id not in gs.players:
                gs.players[p.player_id] = PlayerState(
                    player_id=p.player_id, player_name=p.player_name,
                    score=p.score, wedges=set(json.loads(p.wedges) if p.wedges else []),
                    is_host=p.is_host,
                )
    finally:
        db.close()

def _sync_game_to_db(room_code: str):
    """Persist in-memory game state to SQLite after each answer."""
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            return
        gs = GAMES.get(room_code)
        if not gs:
            return
        g.status = gs.status
        g.current_question = gs.current_q
        
        for p_state in gs.players.values():
            p = db.query(Player).filter(
                Player.game_id == g.id, Player.player_id == p_state.player_id
            ).first()
            if p:
                p.score = p_state.score
                p.wedges = json.dumps(list(p_state.wedges))
        
        db.commit()
    finally:
        db.close()

def _persist_answer(room_code: str, player_id: str, question_id: int,
                     question_num: int, answer_text: str, is_correct: bool,
                     time_ms: int, pts: int):
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            return
        p = db.query(Player).filter(Player.game_id == g.id, Player.player_id == player_id).first()
        if not p:
            return
        db.add(Answer(
            game_id=g.id, player_id=p.id, question_id=question_id,
            question_num=question_num, answer_text=answer_text,
            is_correct=is_correct, time_taken_ms=time_ms, points_earned=pts,
        ))
        db.commit()
        # Update player stats
        ps = db.query(PlayerStats).filter(PlayerStats.player_name == p.player_name).first()
        if not ps:
            ps = PlayerStats(player_name=p.player_name)
            db.add(ps)
        # games_played updated on game end only
        ps.total_score = (ps.total_score or 0) + pts
        ps.total_correct = (ps.total_correct or 0) + (1 if is_correct else 0)
        ps.total_asked = (ps.total_asked or 0) + 1
        ps.last_played_at = int(datetime.now(timezone.utc).timestamp())
        db.commit()
    finally:
        db.close()

@router.post("", response_model=GameResponse)
async def create_game(data: GameCreate, background_tasks: BackgroundTasks):
    room_code = generate_room_code()
    db = SessionLocal()
    try:
        if data.profile_id:
            p = db.query(Profile).filter(Profile.id == data.profile_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Profile not found")
            if not p.consent_obtained:
                raise HTTPException(status_code=403, detail="Consent not obtained from guest. Generate a consent link first.")
        # Validate consent for all profile_ids (single or multi)
        profile_ids = []
        if data.things:
            profile_ids = [t.profile_id for t in data.things]
            if len(profile_ids) > 10:
                raise HTTPException(status_code=400, detail="Maximum 10 things per game")
            for pid in profile_ids:
                p = db.query(Profile).filter(Profile.id == pid).first()
                if not p:
                    raise HTTPException(status_code=404, detail=f"Profile {pid} not found")
                if not p.consent_obtained:
                    raise HTTPException(status_code=403, detail=f"Consent not obtained for profile {pid}")
        elif data.profile_id:
            profile_ids = [data.profile_id]
            p = db.query(Profile).filter(Profile.id == data.profile_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Profile not found")
            if not p.consent_obtained:
                raise HTTPException(status_code=403, detail="Consent not obtained from guest. Generate a consent link first.")
        
        things_json = json.dumps([t.model_dump() for t in data.things]) if data.things else None
        g = GameSession(room_code=room_code, profile_id=data.profile_id, things=things_json)
        db.add(g)
        db.commit()
        db.refresh(g)
        # Seed the in-memory GameState under the room lock so a concurrent
        # /join or /start for the same room_code (extremely unlikely but
        # possible under room-code collision — should never happen because
        # generate_room_code checks the dict) doesn't race.
        lock = await get_room_lock(room_code)
        async with lock:
            _get_or_create_game_locked(room_code, data.profile_id)

        # Fire parallel scrapes for any thing profile that is still pending/scraping
        if data.things:
            for t in data.things:
                p = db.query(Profile).filter(Profile.id == t.profile_id).first()
                if p and p.scrape_status in ("pending", "scraping"):
                    background_tasks.add_task(trigger_scrape, t.profile_id)
        return GameResponse(
            id=g.id, room_code=g.room_code, profile_id=g.profile_id,
            status=g.status, current_question=g.current_question,
            total_questions=g.total_questions, players=[],
            created_at=g.created_at,
            things=json.loads(things_json) if things_json else None,
        )
    finally:
        db.close()

@router.get("/{room_code}", response_model=GameResponse)
async def get_game(room_code: str):
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            raise HTTPException(status_code=404, detail="Room not found")
        # _load_game_to_memory mutates the in-memory GameState; guard it
        # with the room lock so we don't race against concurrent join or
        # start_game handlers.
        lock = await get_room_lock(room_code)
        async with lock:
            _load_game_to_memory(room_code)
            gs = GAMES.get(room_code)
        players = []
        for p in g.players:
            wedges = []
            if gs and p.player_id in gs.players:
                wedges = list(gs.players[p.player_id].wedges)
            players.append(PlayerResponse(
                id=p.id, player_id=p.player_id, player_name=p.player_name,
                score=p.score, wedges=wedges or json.loads(p.wedges or "[]"),
                is_host=p.is_host, is_active=p.is_active,
            ))
        return GameResponse(
            id=g.id, room_code=g.room_code, profile_id=g.profile_id,
            status=gs.status if gs else g.status,
            current_question=gs.current_q if gs else g.current_question,
            total_questions=g.total_questions, players=players,
            created_at=g.created_at,
        )
    finally:
        db.close()

@router.post("/{room_code}/join", response_model=PlayerResponse)
async def join_game(room_code: str, data: PlayerJoin):
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            raise HTTPException(status_code=404, detail="Room not found")
        if g.status != "lobby":
            raise HTTPException(status_code=400, detail="Game already started")

        existing = db.query(Player).filter(
            Player.game_id == g.id, Player.player_name == data.player_name
        ).first()

        # Acquire the room lock before mutating GAMES / GameState. We do it
        # after the DB reads above so the lock window is tight, and before
        # any mutation. For the existing-player branch we hold the lock only
        # across the in-memory state mutation (no broadcast after). For the
        # new-player branch we snapshot the player list inside the lock and
        # release before await broadcast() so slow WS sends don't block
        # other room operations.
        lock = await get_room_lock(room_code)

        if existing:
            # Use the DB record player_id as authoritative identity
            player_id = existing.player_id
            existing.is_active = True
            db.commit()
            async with lock:
                gs = _get_or_create_game_locked(room_code, g.profile_id)
                gs.players[player_id] = PlayerState(
                    player_id=player_id, player_name=data.player_name,
                    score=existing.score, wedges=set(json.loads(existing.wedges or "[]")),
                    is_host=existing.is_host,
                )
            return PlayerResponse(
                id=existing.id, player_id=existing.player_id,
                player_name=existing.player_name, score=existing.score,
                wedges=json.loads(existing.wedges or "[]"),
                is_host=existing.is_host, is_active=True,
            )

        player_id = data.player_id or f"p_{random.randint(100000,999999)}"
        p = Player(game_id=g.id, player_id=player_id, player_name=data.player_name)
        db.add(p)
        db.commit()
        db.refresh(p)

        async with lock:
            gs = _get_or_create_game_locked(room_code, g.profile_id)
            gs.players[player_id] = PlayerState(
                player_id=player_id, player_name=data.player_name,
            )
            # Snapshot player list inside the lock so the broadcast payload
            # matches the state at the moment of the join.
            players_snapshot = [
                {"player_id": pid, "player_name": ps.player_name,
                 "score": ps.score, "wedges": list(ps.wedges), "is_host": ps.is_host}
                for pid, ps in gs.players.items()
            ]
            player_count = len(gs.players)

        await broadcast(room_code, {
            "type": "player_joined",
            "player_id": player_id,
            "player_name": data.player_name,
            "player_count": player_count,
            "players": players_snapshot,
        })

        return PlayerResponse(
            id=p.id, player_id=p.player_id, player_name=data.player_name,
            score=0, wedges=[], is_host=False, is_active=True,
        )
    finally:
        db.close()

@router.post("/{room_code}/start")
async def start_game(room_code: str):
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            raise HTTPException(status_code=404, detail="Room not found")
        
        # Check consent if profile is set
        if g.profile_id:
            profile = db.query(Profile).filter(Profile.id == g.profile_id).first()
            if profile and not profile.consent_obtained:
                raise HTTPException(status_code=403, detail="Guest consent not obtained. Generate a consent link first.")

        # Load questions from all things' profile_ids, merge pools
        # g.things defaults to "[]" (truthy string) — branch on the parsed
        # list, not the raw column, or plain profile_id games can never start.
        thing_list = json.loads(g.things) if g.things else []
        if thing_list:
            all_qs = []
            for thing in thing_list:
                pid = thing.get("profile_id")
                n = thing.get("num_questions", 50)
                qs = db.query(Question).filter(Question.profile_id == pid).all()
                if qs:
                    # Honor each thing's num_questions allotment instead of pooling
                    # everything and slicing by the flat game default.
                    random.shuffle(qs)
                    all_qs.extend(qs[:n])
            if not all_qs:
                raise HTTPException(status_code=400, detail="No questions available for these profiles")
            random.shuffle(all_qs)
            selected = all_qs
        else:
            qs = db.query(Question).filter(Question.profile_id == g.profile_id).all()
            if not qs:
                raise HTTPException(status_code=400, detail="No questions available for this profile")
            random.shuffle(qs)
            selected = qs[:g.total_questions]

        # Acquire the room lock before mutating GAMES / GameState so a
        # concurrent join_game() can't slip a player into gs.players after
        # we've snapshotted it for the game_started broadcast.
        lock = await get_room_lock(room_code)

        async with lock:
            gs = _get_or_create_game_locked(room_code, g.profile_id)
            gs.questions = [TriviaQuestion(
                category=q.category, question_text=q.question_text,
                correct_answer=q.correct_answer,
                wrong_answers=json.loads(q.wrong_answers) if q.wrong_answers else [],
                difficulty=q.difficulty,
            ) for q in selected]
            # Sync the real question count to both DB and in-memory state so
            # total_questions reflects the actual pool (multi-thing = sum of
            # per-thing allotments) and next_question() can reach completion.
            g.total_questions = len(gs.questions)
            gs.total_q = len(gs.questions)
            gs.status = "active"
            gs.question_started_at = datetime.now(timezone.utc).timestamp()

            # First player who joined is host (or designated host)
            if g.players:
                host = sorted(g.players, key=lambda x: x.id)[0]
                host.is_host = True
                db.query(Player).filter(Player.id == host.id).update({"is_host": True})
                if host.player_id in gs.players:
                    gs.players[host.player_id].is_host = True

            g.status = "active"
            db.commit()

            # Snapshot the data needed for broadcasts inside the lock so
            # the events match the state at the moment we transitioned to
            # active.
            player_count = len(gs.players)
            total_questions = len(gs.questions)
            first_question_payload = None
            q = gs.current_question()
            if q:
                elapsed = _time_module.time() - gs.question_started_at
                remaining = max(0, int(gs.question_time_limit - elapsed))
                first_question_payload = {
                    "type": "new_question",
                    "question_num": gs.current_q + 1,
                    "total_questions": len(gs.questions),
                    "category": q.category,
                    "category_color": CATEGORY_COLORS.get(q.category, "#ffffff"),
                    "question_text": q.question_text,
                    "options": [q.correct_answer] + list(q.wrong_answers),
                    "timer_seconds": remaining,
                }

        # Broadcasts released from the lock so a slow WS send doesn't block
        # other room operations.
        await broadcast(room_code, {
            "type": "game_started",
            "room_code": room_code,
            "player_count": player_count,
            "total_questions": total_questions,
        })
        if first_question_payload is not None:
            await broadcast(room_code, first_question_payload)
        return {"ok": True, "total_questions": total_questions}
    finally:
        db.close()

@router.get("/{room_code}/question")
def get_question(room_code: str):
    gs = GAMES.get(room_code)
    if not gs:
        raise HTTPException(status_code=404, detail="Game not found")
    q = gs.current_question()
    if not q:
        raise HTTPException(status_code=400, detail="No more questions")
    
    elapsed = _time_module.time() - gs.question_started_at
    remaining = max(0, int(gs.question_time_limit - elapsed))
    
    # Shuffle a copy so the cached question object is not mutated.
    # Options always include the correct answer + all wrong answers.
    options = [q.correct_answer] + list(q.wrong_answers)
    random.shuffle(options)

    return QuestionDisplay(
        question_num=gs.current_q + 1,
        total_questions=gs.total_q,
        category=q.category,
        category_color=CATEGORY_COLORS.get(q.category, "#ffffff"),
        question_text=q.question_text,
        options=options,
        timer_seconds=remaining,
    )

@router.post("/{room_code}/answer", response_model=AnswerResponse)
async def submit_answer(room_code: str, data: AnswerSubmit):
    # Acquire the room lock for the entire critical section: state mutation,
    # wedge-win detection, DB write, and broadcast payload assembly. The
    # broadcast itself is awaited inside the lock so listeners receive the
    # answer_result and any game_over event atomically with the state change.
    lock = await get_room_lock(room_code)
    async with lock:
        gs = GAMES.get(room_code)
        if not gs:
            raise HTTPException(status_code=404, detail="Game not found")
        if data.player_id not in gs.players:
            raise HTTPException(status_code=404, detail="Player not in game")

        # If the game is already finished (e.g., a concurrent /answer
        # already triggered the wedge-win), reject late answers without
        # mutating state or broadcasting. Without this check both
        # concurrent arrivals would see all_wedges_earned() == True and
        # each fire its own game_over broadcast.
        if gs.status == "finished":
            raise HTTPException(status_code=400, detail="Game already finished")

        q = gs.current_question()
        if not q:
            raise HTTPException(status_code=400, detail="No active question")

        is_correct, pts = gs.record_answer(
            data.player_id, data.answer_text, data.time_taken_ms,
        )

        # SPEC: first player to complete all 6 category wedges wins outright.
        # If this answer just earned the winning wedge, end the game now.
        # We check `status` after record_answer too: another concurrent
        # request that won the race could have flipped it to "finished".
        # (Under the per-room lock that other request serialized before us,
        # so this would only be true if the state was finished before this
        # call entered the critical section.)
        game_ended_by_wedges = gs.all_wedges_earned() and gs.status != "finished"
        if game_ended_by_wedges:
            gs.status = "finished"

        _sync_game_to_db(room_code)

        # Snapshot broadcast payloads inside the lock so concurrent mutators
        # can't change the dict between snapshot and send.
        answer_payload = {
            "type": "answer_result",
            "player_id": data.player_id,
            "player_name": gs.players[data.player_id].player_name,
            "is_correct": is_correct,
            "correct_answer": q.correct_answer,
            "points_earned": pts,
            "player_scores": {
                pid: {"player_name": ps.player_name, "score": ps.score, "wedges": list(ps.wedges)}
                for pid, ps in gs.players.items()
            },
        }
        game_over_payload = None
        if game_ended_by_wedges:
            game_over_payload = {
                "type": "game_over",
                "room_code": room_code,
                "reason": "wedges",
                "winner_player_id": data.player_id,
                "winner_player_name": gs.players[data.player_id].player_name,
                "winner_wedges": sorted(gs.players[data.player_id].wedges),
                "final_scores": gs.get_scores(),
            }

        # _persist_answer opens its own DB session. Run it under the room
        # lock too: it's a state write (Answer row + PlayerStats bump) and
        # we don't want another answer for the same room to interleave
        # its DB write with this one.
        db = SessionLocal()
        try:
            g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
            p_row = db.query(Player).filter(
                Player.game_id == g.id, Player.player_id == data.player_id
            ).first()
            if g and p_row:
                _persist_answer(
                    room_code, data.player_id, None, gs.current_q + 1,
                    data.answer_text, is_correct, data.time_taken_ms, pts,
                )
        finally:
            db.close()

        # Broadcast inside the lock so the WS events match the state at
        # the moment of release. broadcast() awaits send_json() per player;
        # holding the lock during that means concurrent /answer calls for
        # the same room serialize cleanly.
        await broadcast(room_code, answer_payload)
        if game_over_payload is not None:
            await broadcast(room_code, game_over_payload)
            # Cleanup happens after the game_over broadcast reaches all
            # listeners so the final state is visible.
            await _finalize_game_stats(room_code)

        return AnswerResponse(
            player_id=data.player_id,
            player_name=gs.players[data.player_id].player_name,
            is_correct=is_correct,
            points_earned=pts,
            correct_answer=q.correct_answer,
            time_taken_ms=data.time_taken_ms,
        )


async def _finalize_game_stats(room_code: str):
    """Increment games_played for all active players; mark winner; clean up in-memory game.

    MUST be called under the room lock (caller holds it). The DB read+write
    here is not awaited — SQLite is synchronous — so the critical section
    covers everything.
    """
    gs = GAMES.get(room_code)
    if not gs:
        return
    winner = gs.winner()
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            return
        for p_state in gs.players.values():
            if not p_state.is_active:
                continue
            p = db.query(Player).filter(
                Player.game_id == g.id, Player.player_id == p_state.player_id
            ).first()
            if not p:
                continue
            ps = db.query(PlayerStats).filter(PlayerStats.player_name == p.player_name).first()
            if not ps:
                ps = PlayerStats(player_name=p.player_name)
                db.add(ps)
            ps.games_played = (ps.games_played or 0) + 1
            if winner and p_state.player_id == winner.player_id:
                ps.games_won = (ps.games_won or 0) + 1
            ps.last_played_at = int(datetime.now(timezone.utc).timestamp())
        db.commit()
    finally:
        db.close()
    # Remove from in-memory GAMES dict to prevent unbounded growth. Holding
    # _REGISTRY_LOCK inside cleanup_game is fine: it's not the per-room lock.
    await cleanup_game(room_code)

@router.post("/{room_code}/next")
async def next_question(room_code: str):
    # Same lock + critical-section pattern as submit_answer: hold the room
    # lock across state mutation, DB write, and broadcast.
    lock = await get_room_lock(room_code)
    async with lock:
        gs = GAMES.get(room_code)
        if not gs:
            # Resume from DB (container restart recovery)
            db = SessionLocal()
            try:
                g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
                if not g or g.status == "finished":
                    raise HTTPException(status_code=404, detail="Game not found")
                # Reconstruct minimal game state from DB for resume
                from app.services.game_engine import GameState
                gs = GameState(room_code=room_code, profile_id=g.profile_id, total_q=g.total_questions)
                gs.status = g.status
                gs.current_q = g.current_question
                GAMES[room_code] = gs
            finally:
                db.close()
        if not gs:
            raise HTTPException(status_code=404, detail="Game not found")
        if gs.status == "finished":
            # Game already ended (e.g., wedge win in /answer) — don't advance.
            raise HTTPException(status_code=400, detail="Game already finished")
        gs.next_question()
        # SPEC: question exhaustion ends the game with highest-score winner.
        if gs.current_q >= gs.total_q:
            gs.status = "finished"
        db = SessionLocal()
        try:
            g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
            if g:
                g.current_question = gs.current_q
                g.status = gs.status
                db.commit()
        finally:
            db.close()

        # Snapshot broadcast payloads inside the lock.
        if gs.status == "finished":
            winner = gs.winner()
            broadcast_payload = {
                "type": "game_over",
                "room_code": room_code,
                "reason": "exhaustion",
                "winner_player_id": winner.player_id if winner else None,
                "winner_player_name": winner.player_name if winner else None,
                "winner_wedges": sorted(winner.wedges) if winner else [],
                "final_scores": gs.get_scores(),
            }
        else:
            q = gs.current_question()
            broadcast_payload = None
            if q:
                broadcast_payload = {
                    "type": "new_question",
                    "question_num": gs.current_q + 1,
                    "total_questions": len(gs.questions),
                    "category": q.category,
                    "category_color": CATEGORY_COLORS.get(q.category, "#ffffff"),
                    "question_text": q.question_text,
                    "options": [q.correct_answer] + list(q.wrong_answers),
                    "timer_seconds": gs.question_time_limit,
                }

        # Broadcast + cleanup inside the lock so listeners and state are
        # consistent with each other.
        if broadcast_payload is not None:
            await broadcast(room_code, broadcast_payload)
        if gs.status == "finished":
            await _finalize_game_stats(room_code)

        return {"ok": True, "current_question": gs.current_q + 1, "status": gs.status}

@router.get("/{room_code}/scores")
def get_scores(room_code: str):
    gs = GAMES.get(room_code)
    if not gs:
        raise HTTPException(status_code=404, detail="Game not found")
    return gs.get_scores()
