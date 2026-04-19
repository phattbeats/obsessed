import random, json
from fastapi import APIRouter, HTTPException
import time as _time_module
from app.database import SessionLocal, Profile, Question, GameSession, Player, Answer, PlayerStats
from app.models import (
    GameCreate, GameResponse, PlayerJoin, PlayerResponse,
    AnswerSubmit, AnswerResponse, QuestionDisplay,
)
from app.services.game_engine import (
    GAMES, GameState, TriviaQuestion, PlayerState,
    get_or_create_game, generate_room_code, cleanup_game,
)
from datetime import datetime

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
    """Sync a game from SQLite into in-memory GameState."""
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            return
        gs = get_or_create_game(room_code, g.profile_id)
        gs.status = g.status
        gs.current_q = g.current_question
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
        ps.last_played_at = int(datetime.utcnow().timestamp())
        db.commit()
    finally:
        db.close()

@router.post("", response_model=GameResponse)
def create_game(data: GameCreate):
    room_code = generate_room_code()
    db = SessionLocal()
    try:
        if data.profile_id:
            p = db.query(Profile).filter(Profile.id == data.profile_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Profile not found")
            if not p.consent_obtained:
                raise HTTPException(status_code=403, detail="Consent not obtained from guest. Generate a consent link first.")
        g = GameSession(room_code=room_code, profile_id=data.profile_id)
        db.add(g)
        db.commit()
        db.refresh(g)
        get_or_create_game(room_code, data.profile_id)
        return GameResponse(
            id=g.id, room_code=g.room_code, profile_id=g.profile_id,
            status=g.status, current_question=g.current_question,
            total_questions=g.total_questions, players=[],
            created_at=g.created_at,
        )
    finally:
        db.close()

@router.get("/{room_code}", response_model=GameResponse)
def get_game(room_code: str):
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        if not g:
            raise HTTPException(status_code=404, detail="Room not found")
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
def join_game(room_code: str, data: PlayerJoin):
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
        if existing:
            # Use the DB record player_id as authoritative identity
            player_id = existing.player_id
            existing.is_active = True
            db.commit()
            gs = get_or_create_game(room_code, g.profile_id)
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
        
        gs = get_or_create_game(room_code, g.profile_id)
        gs.players[player_id] = PlayerState(player_id=player_id, player_name=data.player_name)
        
        return PlayerResponse(
            id=p.id, player_id=p.player_id, player_name=p.player_name,
            score=0, wedges=[], is_host=False, is_active=True,
        )
    finally:
        db.close()

@router.post("/{room_code}/start")
def start_game(room_code: str):
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

        # Load questions
        qs = db.query(Question).filter(Question.profile_id == g.profile_id).all()
        if not qs:
            raise HTTPException(status_code=400, detail="No questions available for this profile")
        
        random.shuffle(qs)
        selected = qs[:g.total_questions]
        
        gs = get_or_create_game(room_code, g.profile_id)
        gs.questions = [TriviaQuestion(
            category=q.category, question_text=q.question_text,
            correct_answer=q.correct_answer,
            wrong_answers=json.loads(q.wrong_answers) if q.wrong_answers else [],
            difficulty=q.difficulty,
        ) for q in selected]
        gs.status = "active"
        gs.question_started_at = datetime.utcnow().timestamp()
        
        # First player who joined is host (or designated host)
        if g.players:
            host = sorted(g.players, key=lambda x: x.id)[0]
            host.is_host = True
            db.query(Player).filter(Player.id == host.id).update({"is_host": True})
            if host.player_id in gs.players:
                gs.players[host.player_id].is_host = True
        
        g.status = "active"
        db.commit()
        return {"ok": True, "total_questions": len(gs.questions)}
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
    
    # Only wrong answers shown to players — correct answer revealed after submit
    options = q.wrong_answers
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
def submit_answer(room_code: str, data: AnswerSubmit):
    gs = GAMES.get(room_code)
    if not gs:
        raise HTTPException(status_code=404, detail="Game not found")
    if data.player_id not in gs.players:
        raise HTTPException(status_code=404, detail="Player not in game")
    
    q = gs.current_question()
    if not q:
        raise HTTPException(status_code=400, detail="No active question")
    
    is_correct, pts = gs.record_answer(data.player_id, data.answer_text, data.time_taken_ms)
    _sync_game_to_db(room_code)
    
    db = SessionLocal()
    try:
        g = db.query(GameSession).filter(GameSession.room_code == room_code).first()
        p_row = db.query(Player).filter(
            Player.game_id == g.id, Player.player_id == data.player_id
        ).first()
        if g and p_row:
            _persist_answer(room_code, data.player_id, None, gs.current_q + 1,
                           data.answer_text, is_correct, data.time_taken_ms, pts)
        return AnswerResponse(
            player_id=data.player_id,
            player_name=gs.players[data.player_id].player_name,
            is_correct=is_correct,
            points_earned=pts,
            correct_answer=q.correct_answer,
            time_taken_ms=data.time_taken_ms,
        )
    finally:
        db.close()


def _finalize_game_stats(room_code: str):
    """Increment games_played for all active players; mark winner; clean up in-memory game."""
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
            ps.last_played_at = int(datetime.utcnow().timestamp())
        db.commit()
    finally:
        db.close()
    # Remove from in-memory GAMES dict to prevent unbounded growth
    cleanup_game(room_code)

@router.post("/{room_code}/next")
def next_question(room_code: str):
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
            gs = GameState(profile_id=g.profile_id, num_questions=g.total_questions)
            gs.status = g.status
            gs.current_q = g.current_question
            GAMES[room_code] = gs
        finally:
            db.close()
    if not gs:
        raise HTTPException(status_code=404, detail="Game not found")
    gs.next_question()
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
    if gs.status == "finished":
        _finalize_game_stats(room_code)
    return {"ok": True, "current_question": gs.current_q + 1, "status": gs.status}

@router.get("/{room_code}/scores")
def get_scores(room_code: str):
    gs = GAMES.get(room_code)
    if not gs:
        raise HTTPException(status_code=404, detail="Game not found")
    return gs.get_scores()
