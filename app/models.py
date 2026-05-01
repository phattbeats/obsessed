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
    pinterest_handle: str = ""
    threads_handle: str = ""
    instagram_handle: str = ""
    google_places_handle: str = ""
    wikipedia_handle: str = ""
    osm_query: str = ""
    travel_url: str = ""
    wikidata_query: str = ""
    openlibrary_query: str = ""
    gdelt_query: str = ""
    manual_link: str = ""
    manual_facts: str = ""
    question_budget: int = 50
    consent_obtained: bool = False
    content_quality: str = ""
    content_chunks: int = 0

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    reddit_handle: Optional[str] = None
    twitter_handle: Optional[str] = None
    steam_id: Optional[str] = None
    discord_handle: Optional[str] = None
    pinterest_handle: Optional[str] = None
    threads_handle: Optional[str] = None
    instagram_handle: Optional[str] = None
    google_places_handle: Optional[str] = None
    wikipedia_handle: Optional[str] = None
    osm_query: Optional[str] = None
    travel_url: Optional[str] = None
    wikidata_query: Optional[str] = None
    openlibrary_query: Optional[str] = None
    gdelt_query: Optional[str] = None
    manual_link: Optional[str] = None
    manual_facts: Optional[str] = None
    llm_calls: Optional[int] = None
    llm_spend_cents: Optional[int] = None
    question_budget: Optional[int] = None
    consent_obtained: Optional[bool] = None
    content_quality: Optional[str] = None
    content_chunks: Optional[int] = None

class ProfileResponse(BaseModel):
    id: int
    name: str
    bio: str
    reddit_handle: str
    twitter_handle: str
    steam_id: str
    discord_handle: str
    pinterest_handle: str
    threads_handle: str
    instagram_handle: str
    google_places_handle: str
    wikipedia_handle: str
    osm_query: str
    travel_url: str
    wikidata_query: str
    openlibrary_query: str
    gdelt_query: str
    manual_link: str
    manual_facts: str
    scrape_status: str
    scrape_error: str
    question_count: int
    llm_calls: int
    llm_spend_cents: int
    question_budget: int
    consent_obtained: bool
    content_quality: str
    content_chunks: int
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
