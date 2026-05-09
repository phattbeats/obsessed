from pydantic_settings import BaseSettings
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    app_name: str = "Obsessed"
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/trivia.db"
    litellm_base: str = "http://10.0.0.100:4000"  # override via LITELLM_BASE env var
    litellm_model: str = "claude-3-5-sonnet-20241022"  # override via LITELLM_MODEL env var
    litellm_api_key: str | None = None  # read from LITELLM_API_KEY env var or .env
    question_count: int = 50
    question_timeout: int = 30  # seconds per question
    ws_heartbeat: int = 30
    categories: list[str] = ["history", "entertainment", "geography", "science", "sports", "art_literature"]
    
    content_max_chars: int = 200000  # cap per scraper source; configurable via CONTENT_MAX_CHARS env var
    class Config:
        env_file = ".env"

settings = Settings()
