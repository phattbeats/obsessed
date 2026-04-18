from pydantic_settings import BaseSettings
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    app_name: str = "Obsessed"
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/trivia.db"
    litellm_base: str = "http://10.0.0.100:4000"
    litellm_api_key: str = "sk-vantage"
    question_count: int = 50
    question_timeout: int = 30  # seconds per question
    ws_heartbeat: int = 30
    categories: list[str] = ["history", "entertainment", "geography", "science", "sports", "art_literature"]
    
    class Config:
        env_file = ".env"

settings = Settings()
