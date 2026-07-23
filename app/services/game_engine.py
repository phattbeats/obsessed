import uuid, random, json, time, asyncio
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class TriviaQuestion:
    category: str
    question_text: str
    correct_answer: str
    wrong_answers: list[str]
    difficulty: int = 1

@dataclass
class PlayerState:
    player_id: str
    player_name: str
    score: int = 0
    wedges: set = field(default_factory=set)
    answered_current: bool = False
    last_answer_correct: bool = False
    is_host: bool = False
    is_active: bool = True  # get_scores()/winner()/all_answered() filter on this

@dataclass
class GameState:
    room_code: str
    profile_id: Optional[int]
    status: str = "lobby"  # lobby → active → finished
    questions: list[TriviaQuestion] = field(default_factory=list)
    current_q: int = 0
    total_q: int = 50
    players: dict[str, PlayerState] = field(default_factory=dict)
    question_started_at: float = 0
    question_time_limit: int = 30

    def current_question(self) -> Optional[TriviaQuestion]:
        if 0 <= self.current_q < len(self.questions):
            return self.questions[self.current_q]
        return None

    def next_question(self):
        self.current_q += 1
        self.question_started_at = time.time()
        for p in self.players.values():
            p.answered_current = False

    def record_answer(self, player_id: str, answer: str, time_ms: int) -> tuple[bool, int]:
        q = self.current_question()
        if not q:
            return False, 0
        correct = answer.strip().lower() == q.correct_answer.strip().lower()
        pts = 0
        if correct:
            time_s = max(0, time_ms / 1000)
            bonus = max(0, int(1000 * (1 - time_s / self.question_time_limit)))
            pts = 1000 + bonus
            if q.category not in self.players[player_id].wedges:
                self.players[player_id].wedges.add(q.category)
        self.players[player_id].score += pts
        self.players[player_id].answered_current = True
        self.players[player_id].last_answer_correct = correct
        return correct, pts

    def all_answered(self) -> bool:
        return all(p.answered_current for p in self.players.values() if p.is_active)

    def get_scores(self) -> list[dict]:
        return sorted(
            [{"player_id": p.player_id, "player_name": p.player_name,
              "score": p.score, "wedges": list(p.wedges), "is_active": p.is_active}
             for p in self.players.values() if p.is_active],
            key=lambda x: x["score"], reverse=True
        )

    def winner(self) -> Optional[PlayerState]:
        active = [p for p in self.players.values() if p.is_active]
        if not active:
            return None
        # SPEC: first to complete all 6 category wedges wins outright.
        # If anyone has all 6 wedges, that player wins (regardless of score).
        wedge_winners = [p for p in active if len(p.wedges) >= 6]
        if wedge_winners:
            return wedge_winners[0]
        return max(active, key=lambda p: p.score)

    def wedge_winner(self) -> Optional[PlayerState]:
        """Return the player who first completed all 6 wedges, or None."""
        active = [p for p in self.players.values() if p.is_active]
        if not active:
            return None
        for p in active:
            if len(p.wedges) >= 6:
                return p
        return None

    def all_wedges_earned(self) -> bool:
        return any(len(p.wedges) >= 6 for p in self.players.values())

# In-memory game state, persisted to SQLite on game end
GAMES: dict[str, GameState] = {}

# Per-room asyncio locks guarding mutation of GAMES[room_code] and its
# GameState. Lazily created alongside the GameState.
#
# Process-local: a multi-worker uvicorn deployment would have one of these
# dicts per worker and requests for the same room could land on different
# workers. For multi-worker correctness, move state to Redis (or similar)
# and acquire a Redis lock instead. Single-worker uvicorn is fine as-is.
GAME_LOCKS: dict[str, asyncio.Lock] = {}

# Meta-lock for mutations of GAMES / GAME_LOCKS themselves (creating locks,
# popping on cleanup). Never held during the per-room critical section.
_REGISTRY_LOCK = asyncio.Lock()


async def get_room_lock(room_code: str) -> asyncio.Lock:
    """Return the asyncio.Lock for `room_code`, creating one if needed.

    Safe to call concurrently: the dict mutation is guarded by _REGISTRY_LOCK.
    Fast path is unlocked (Lock creation is lazy and one-shot per room).
    """
    lock = GAME_LOCKS.get(room_code)
    if lock is not None:
        return lock
    async with _REGISTRY_LOCK:
        lock = GAME_LOCKS.get(room_code)
        if lock is None:
            lock = asyncio.Lock()
            GAME_LOCKS[room_code] = lock
        return lock


def generate_room_code() -> str:
    while True:
        code = f"{random.randint(0,999999):06d}"
        if code not in GAMES:
            return code

async def get_or_create_game(room_code: str, profile_id: Optional[int] = None) -> GameState:
    """Return the GameState for `room_code`, creating one if needed.

    Holds the room lock for the duration so the create-then-mutate sequence
    is atomic with respect to other operations on the same room.

    NOTE: callers that already hold the room lock should call
    `_get_or_create_game_locked` directly to avoid re-entrant deadlock.
    asyncio.Lock is not re-entrant.
    """
    lock = await get_room_lock(room_code)
    async with lock:
        return _get_or_create_game_locked(room_code, profile_id)


def _get_or_create_game_locked(room_code: str, profile_id: Optional[int] = None) -> GameState:
    """Lock-free variant of get_or_create_game for callers that already hold
    the room lock for `room_code`. asyncio.Lock is not re-entrant, so any
    caller that has done `async with await get_room_lock(room_code):` MUST
    use this helper rather than `await get_or_create_game(...)`.
    """
    if room_code not in GAMES:
        GAMES[room_code] = GameState(room_code=room_code, profile_id=profile_id)
    elif profile_id is not None and GAMES[room_code].profile_id is None:
        # Only set profile_id on first assignment, not on subsequent resumes
        GAMES[room_code].profile_id = profile_id
    return GAMES[room_code]


async def cleanup_game(room_code: str):
    """Remove a game's in-memory state and its lock.

    Called from _finalize_game_stats after a game ends. Safe to call when
    the room is already gone (no-op).
    """
    async with _REGISTRY_LOCK:
        GAMES.pop(room_code, None)
        GAME_LOCKS.pop(room_code, None)