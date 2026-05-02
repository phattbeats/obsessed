from sqlalchemy import create_engine, Column, Integer, String, Text, Float, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import json, sqlite3, os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "trivia.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Profile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    bio = Column(Text, default="")
    reddit_handle = Column(String(200), default="")
    twitter_handle = Column(String(200), default="")
    steam_id = Column(String(200), default="")
    discord_handle = Column(String(200), default="")
    pinterest_handle = Column(String(200), default="")
    threads_handle = Column(String(200), default="")
    instagram_handle = Column(String(200), default="")
    google_places_handle = Column(String(500), default="")  # comma-separated business names
    wikipedia_handle = Column(String(500), default="")  # place name for Wikipedia scrape
    osm_query = Column(String(500), default="")  # place name for OpenStreetMap
    travel_url = Column(String(500), default="")  # travel blog or TripAdvisor URL
    wikidata_query = Column(String(500), default="")  # Wikidata search query (for things)
    openlibrary_query = Column(String(500), default="")  # OpenLibrary search query (for things)
    gdelt_query = Column(String(500), default="")  # GDELT events search query (for events)
    entity_type = Column(String(20), default="person")  # person|place|thing|event
    manual_link = Column(String(500), default="")
    manual_facts = Column(Text, default="")
    scrape_status = Column(String(50), default="pending")  # pending|scraping|done|failed
    scrape_error = Column(Text, default="")
    raw_content = Column(Text, default="")
    question_count = Column(Integer, default=0)
    llm_calls = Column(Integer, default=0)
    llm_spend_cents = Column(Integer, default=0)
    question_budget = Column(Integer, default=50)
    consent_obtained = Column(Boolean, default=False)
    consent_token = Column(String(200), default="")
    content_quality = Column(String(20), default="")  # insufficient|limited|adequate|rich
    content_chunks = Column(Integer, default=0)
    created_at = Column(Integer, default=lambda: int(datetime.utcnow().timestamp()))
    updated_at = Column(Integer, default=lambda: int(datetime.utcnow().timestamp()))

    questions = relationship("Question", back_populates="profile", cascade="all, delete-orphan")

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(50), nullable=False)  # history|entertainment|geography|science|sports|art_literature
    question_text = Column(Text, nullable=False)
    correct_answer = Column(Text, nullable=False)
    wrong_answers = Column(Text, nullable=False)  # JSON list: ["wrong1","wrong2","wrong3"]
    difficulty = Column(Integer, default=1)  # 1-3
    source_snippet = Column(Text, default="")
    created_at = Column(Integer, default=lambda: int(datetime.utcnow().timestamp()))
    profile = relationship("Profile", back_populates="questions")

class GameSession(Base):
    __tablename__ = "games"
    id = Column(Integer, primary_key=True, autoincrement=True)
    room_code = Column(String(6), unique=True, nullable=False)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    status = Column(String(50), default="lobby")  # lobby|active|finished
    current_question = Column(Integer, default=0)
    total_questions = Column(Integer, default=50)
    created_at = Column(Integer, default=lambda: int(datetime.utcnow().timestamp()))
    players = relationship("Player", back_populates="game", cascade="all, delete-orphan")
    answers = relationship("Answer", back_populates="game", cascade="all, delete-orphan")

class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(String(36), nullable=False)  # UUID
    player_name = Column(String(200), nullable=False)
    score = Column(Integer, default=0)
    wedges = Column(String(200), default="[]")  # JSON list of earned category names
    is_host = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    joined_at = Column(Integer, default=lambda: int(datetime.utcnow().timestamp()))
    game = relationship("GameSession", back_populates="players")
    answers = relationship("Answer", back_populates="player", cascade="all, delete-orphan")

class Answer(Base):
    __tablename__ = "answers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=True)
    question_num = Column(Integer, nullable=False)
    answer_text = Column(Text, nullable=False)
    is_correct = Column(Boolean, default=False)
    time_taken_ms = Column(Integer, default=0)
    points_earned = Column(Integer, default=0)
    submitted_at = Column(Integer, default=lambda: int(datetime.utcnow().timestamp()))
    game = relationship("GameSession", back_populates="answers")
    player = relationship("Player", back_populates="answers")

class PlayerStats(Base):
    __tablename__ = "player_stats"
    player_name = Column(String(200), primary_key=True)
    games_played = Column(Integer, default=0)
    games_won = Column(Integer, default=0)
    total_score = Column(Integer, default=0)
    total_correct = Column(Integer, default=0)
    total_asked = Column(Integer, default=0)
    last_played_at = Column(Integer, default=0)

def init_db():
    Base.metadata.create_all(bind=engine)
    # Seed categories
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='category_seeds'")
    if not cursor.fetchone():
        cursor.execute("""
            CREATE TABLE category_seeds (id INTEGER PRIMARY KEY, name TEXT UNIQUE, color TEXT, icon TEXT)
        """)
        seeds = [
            ("history", "#ff6d00", "scroll"),
            ("entertainment", "#d500f9", "clapperboard"),
            ("geography", "#2979ff", "globe"),
            ("science", "#00e676", "atom"),
            ("sports", "#ff1744", "ball"),
            ("art_literature", "#ffea00", "palette"),
        ]
        cursor.executemany("INSERT OR IGNORE INTO category_seeds (name, color, icon) VALUES (?,?,?)", seeds)
        conn.commit()
    conn.close()

init_db()
