"""
Reddit scraper for PEOPLE entity type.
Scrapes Reddit user submitted + comments pages.
Rate-limit aware: uses REDDIT_LIMITER to prevent 429s.
"""
import asyncio, httpx, json, re
from typing import Optional
import os
from app.config import settings
from app.services.scraper.rate_limiter import REDDIT_LIMITER, retry_with_backoff

LITELLM_BASE = "http://10.0.0.100:4000"
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
CATEGORIES = ["history", "entertainment", "geography", "science", "sports", "art_literature"]


async def scrape_reddit(handle: str) -> tuple[str, list[dict]]:
    """Scrape Reddit user submitted + comments. Returns (raw_text, posts)."""
    raw = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ObsessedBot/1.0)"}

    async with REDDIT_LIMITER:
        for endpoint in [f"/u/{handle}/submitted.json", f"/u/{handle}/comments.json"]:
            try:
                resp = await retry_with_backoff(
                    lambda: httpx.AsyncClient(timeout=30.0, headers=headers).get(
                        f"https://www.reddit.com{endpoint}?limit=100"
                    ),
                    max_retries=3,
                    base_delay=2.0,
                )
                resp.raise_for_status()
                data = resp.json()
                posts = data.get("data", {}).get("children", [])
                for post in posts:
                    d = post["data"]
                    text = d.get("selftext") or d.get("title", "")
                    if text:
                        raw.append(f"[Reddit {d.get('subreddit','').lower()}] {text}")
            except Exception:
                pass

    return "\n".join(raw), raw


def clean_text(text: str) -> str:
    """Strip URLs, @mentions, and excess whitespace."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"@\w+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from scraped content via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly {50 if len(raw_content) > 500 else 25} trivia questions about them.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- correct_answer and wrong_answers must be full sentences or specific facts
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: the exact phrase from the input that inspired this question (max 20 words)
- Return ONLY the JSON array, no commentary
- If you cannot generate a question for a category, skip it"""

    user_prompt = f"Facts about {name}:\n{raw_content[: settings.content_max_chars]}"

    try:
        api_key = LITELLM_API_KEY or settings.litellm_api_key
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{LITELLM_BASE}/chat/completions",
                json={
                    "model": "claude-3-5-sonnet-20241022",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.8,
                    "max_tokens": 4000,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = re.sub(r"^```json\s*", "", content.strip())
            content = re.sub(r"\s*```$", "", content.strip())
            questions = json.loads(content)
            return questions
    except Exception as e:
        return []