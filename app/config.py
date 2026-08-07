from pydantic_settings import BaseSettings
from pydantic import ConfigDict, AliasChoices, Field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    app_name: str = "Obsessed"
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/trivia.db"
    admin_token: str = ""  # empty = unauthenticated (opt-in lockdown via ADMIN_TOKEN env var)
    litellm_base: str = "http://10.0.0.100:4000"  # override via LITELLM_BASE env var
    # Must name a model the LiteLLM proxy actually serves. The proxy namespaces every
    # deployment by provider, so a bare `claude-*` id 400s with "no healthy deployments"
    # and the generator silently falls back to rule-based questions (PHA-1562).
    litellm_model: str = "anthropic/claude-sonnet-5"  # override via LITELLM_MODEL env var
    # 50 full-sentence questions cost ~8.8k completion tokens. The old 4000 ceiling
    # truncated every response mid-JSON, which read as a generator failure (PHA-1562).
    litellm_max_tokens: int = 16000  # override via LITELLM_MAX_TOKENS env var
    litellm_api_key: str | None = None  # read from LITELLM_API_KEY env var or .env
    steam_api_key: str = ""  # free key from https://steamcommunity.com/dev/apikey
    lastfm_api_key: str = ""  # free key from https://www.last.fm/api/account/create
    crawl4ai_token: str = ""  # bearer for the crawl4ai service; CRAWL4AI_TOKEN env override
    admin_token: str = ""  # if set, /api/admin/* requires Authorization: Bearer <token>; empty = open (LAN-only deploys)
    youtube_api_key: str = ""  # free Data API v3 key (10k units/day) from https://console.cloud.google.com/apis/credentials — fallback only, Innertube is unauthenticated primary
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
    spotify_client_id: str = ""  # from https://developer.spotify.com/dashboard — required for the link-your-account flow
    spotify_redirect_uri: str = "http://localhost:8000/api/profiles/spotify/callback"  # must match the redirect URI registered on the Spotify app
    # No client secret: Authorization Code with PKCE is a public-client flow by design —
    # the code_verifier replaces the secret, so nothing else needs to be kept server-side.
    datadome_solve_proxy: str = ""  # HTTP forward proxy for DataDome solves: USER:PASS@HOST:PORT (must egress same residential IP as the scraper)
    datadome_max_solves_per_run: int = 5  # hard cap on 2Captcha DataDome solve calls per process restart
    familysearch_client_id: str = ""  # free developer key from https://www.familysearch.org/developers/ — required for the Family Tree unauthenticated-session API
    question_count: int = 50
    question_timeout: int = 30  # seconds per question
    ws_heartbeat: int = 30
    categories: list[str] = ["history", "entertainment", "geography", "science", "sports", "art_literature"]

    content_max_chars: int = 200000  # cap per scraper source; configurable via CONTENT_MAX_CHARS env var
    model_config = ConfigDict(env_file=".env")

settings = Settings()