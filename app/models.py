from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

# ─── Profile ───────────────────────────────────────────────────────────────────
class ProfileCreate(BaseModel):
    name: str
    bio: str = ""
    reddit_handle: str = ""
    twitter_handle: str = ""
    steam_id: str = ""
    discord_handle: str = ""
    manual_link: str = ""
    manual_facts: str = ""

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    reddit_handle: Optional[str] = None
    twitter_handle: Optional[str] = None
    steam_id: Optional[str] = None
    discord_handle: Optional[str] = None
    manual_link: Optional[str] = None
    manual_facts: Optional[str] = None

class ProfileResponse(BaseModel):
    id: int
    name: str
    bio: str
    reddit_handle: str
    twitter_handle: str
    steam_id: str
    discord_handle: str
    manual_link: str
    manual_facts: str
    scrape_status: str
    scrape_error: str
    question_count: int
    created_at: int
    updated_at: int

    class Config:
        from_attributes = True

class QuestionResponse(BaseModel):
    id: int
    category: str
    question_text: str
    correct_answer: str
    wrong_answers: list[str]
    difficulty: int
    source_snippet: str

    class Config:
        from_attributes = True

# ─── Game ──────────────────────────────────────────────────────────────────────
class GameCreate(BaseModel):
    profile_id: Optional[int] = None

class GameResponse(BaseModel):
    id: int
    room_code: str
    profile_id: Optional[int]
    status: str
    current_question: int
    total_questions: int
    players: list["PlayerResponse"] = []
    created_at: int

    class Config:
        from_attributes = True

class PlayerJoin(BaseModel):
    player_name: str
    player_id: str = Field(default="")  # UUID generated client-side

class PlayerResponse(BaseModel):
    id: int
    player_id: str
    player_name: str
    score: int
    wedges: list[str]
    is_host: bool
    is_active: bool

    class Config:
        from_attributes = True

class AnswerSubmit(BaseModel):
    player_id: str
    answer_text: str
    time_taken_ms: int = 0

class AnswerResponse(BaseModel):
    player_id: str
    player_name: str
    is_correct: bool
    points_earned: int
    correct_answer: str
    time_taken_ms: int

class QuestionDisplay(BaseModel):
    question_num: int
    total_questions: int
    category: str
    category_color: str
    question_text: str
    options: list[str]
    timer_seconds: int = 30

class LeaderboardEntry(BaseModel):
    player_name: str
    games_played: int
    games_won: int
    total_score: int
    win_rate: float

GameResponse.model_rebuild()
