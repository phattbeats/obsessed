import uuid, random, json, time
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
        return max(active, key=lambda p: p.score)

    def all_wedges_earned(self) -> bool:
        return any(len(p.wedges) >= 6 for p in self.players.values())

# In-memory game state, persisted to SQLite on game end
GAMES: dict[str, GameState] = {}

def generate_room_code() -> str:
    while True:
        code = f"{random.randint(0,999999):06d}"
        if code not in GAMES:
            return code

def get_or_create_game(room_code: str, profile_id: Optional[int] = None) -> GameState:
    if room_code not in GAMES:
        GAMES[room_code] = GameState(room_code=room_code, profile_id=profile_id)
    elif profile_id is not None and GAMES[room_code].profile_id is None:
        # Only set profile_id on first assignment, not on subsequent resumes
        GAMES[room_code].profile_id = profile_id
    return GAMES[room_code]

def cleanup_game(room_code: str):
    if room_code in GAMES:
        del GAMES[room_code]