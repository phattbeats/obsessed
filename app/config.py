from pydantic_settings import BaseSettings
from pydantic import ConfigDict, AliasChoices, Field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    app_name: str = "Obsessed"
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/trivia.db"
    admin_token: str = ""  # empty = unauthenticated (opt-in lockdown via ADMIN_TOKEN env var)
    litellm_base: str = "http://10.0.0.100:4000"  # override via LITELLM_BASE env var
    litellm_model: str = "claude-3-5-sonnet-20241022"  # override via LITELLM_MODEL env var
    litellm_api_key: str | None = None  # read from LITELLM_API_KEY env var or .env
    steam_api_key: str = ""  # free key from https://steamcommunity.com/dev/apikey
    crawl4ai_token: str = ""  # bearer for the crawl4ai service; CRAWL4AI_TOKEN env override
    admin_token: str = ""  # if set, /api/admin/* requires Authorization: Bearer <token>; empty = open (LAN-only deploys)
    # 2captcha.com solver API key; required only when a scraper opts into captcha
    # solving. Canonical env var is TWOCAPTCHA_API_KEY, but we also accept the
    # 2CAPTCHA_API_KEY / TWO_CAPTCHA_API_KEY spellings because the company-secrets
    # store named it `2CAPTCHA_API_KEY` (see PHA-787). Note: a var name starting
    # with a digit isn't a valid POSIX identifier, so some shells/compose loaders
    # silently drop `2CAPTCHA_API_KEY` — prefer TWOCAPTCHA_API_KEY in deploys.
    twocaptcha_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TWOCAPTCHA_API_KEY", "2CAPTCHA_API_KEY", "TWO_CAPTCHA_API_KEY"
        ),
    )
    question_count: int = 50
    question_timeout: int = 30  # seconds per question
    ws_heartbeat: int = 30
    categories: list[str] = ["history", "entertainment", "geography", "science", "sports", "art_literature"]

    content_max_chars: int = 200000  # cap per scraper source; configurable via CONTENT_MAX_CHARS env var
    model_config = ConfigDict(env_file=".env")

settings = Settings()