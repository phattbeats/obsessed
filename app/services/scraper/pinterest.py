import json, re
from typing import Optional

# CRAWL4AI integration
CRAWL4AI_URL = "http://crawl4ai:11235"
CRAWL4AI_TOKEN = "Phatt-tech-2026"

LITELLM_BASE = "http://10.0.0.100:4000"
LITELLM_API_KEY = "sk-vantage"


async def scrape_pinterest(handle: str) -> tuple[str, list[dict]]:
    """
    Scrape a public Pinterest profile using crawl4ai.
    Returns (raw_text, boards_list).
    board entries: {name, pin_count, age, url}
    """
    profile_url = f"https://www.pinterest.com/{handle}/"
    board_sections = []

    try:
        import httpx
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                CRAWL4AI_URL + "/crawl",
                json={
                    "urls": [profile_url],
                    "headless": True,
                    "wait_for": ".userProfilePage",
                    "page_timeout": 30000,
                },
                headers={"Authorization": f"Bearer {CRAWL4AI_TOKEN}"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return "", []

            # markdown is a dict when coming from crawl4ai POST
            raw_md = results[0].get("markdown", {})
            if isinstance(raw_md, dict):
                raw_md = raw_md.get("raw_markdown", "") or raw_md.get("markdown_with_citations", "")
    except Exception as e:
        return f"[Pinterest scrape error: {e}]", []

    # Parse profile name
    profile_name = ""
    lines = raw_md.split("\n")
    for i, line in enumerate(lines):
        # Pinterest profile name is at H1 (# heading)
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            profile_name = m.group(1).strip()
            break

    # Parse boards from raw_markdown
    # Format: [board name](url) , X Pins · , age
    # e.g. "project ideas , 5 Pins · , 5mo"
    board_pattern = re.compile(
        r"\[([^\]]+)\]\((https://www\.pinterest\.com/[^)]+)\)[^\[]*(?:(\d+)\s*Pins)",
        re.IGNORECASE,
    )
    boards = []
    for m in board_pattern.finditer(raw_md):
        name = m.group(1).strip()
        url = m.group(2)
        pin_count = m.group(3) or ""
        boards.append({"name": name, "url": url, "pin_count": pin_count})

    # Deduplicate by name
    seen = set()
    unique_boards = []
    for b in boards:
        if b["name"].lower() not in seen:
            seen.add(b["name"].lower())
            unique_boards.append(b)

    # Build readable text
    lines_out = [f"[Pinterest profile: {profile_name or handle}]"]
    lines_out.append(f"Profile: {profile_name or handle} (https://www.pinterest.com/{handle}/)")
    if unique_boards:
        lines_out.append(f"Interest boards ({len(unique_boards)}):")
        for b in unique_boards:
            age = ""
            # Extract age from markdown context
            pc = f" ({b['pin_count']} Pins)" if b["pin_count"] else ""
            lines_out.append(f"  - {b['name']}{pc} — {b['url']}")
    else:
        lines_out.append("(No boards visible — profile may be private)")

    return "\n".join(lines_out), unique_boards


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from Pinterest content via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly 25 trivia questions about their interests and personality based on their Pinterest boards.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions are about what you can infer about the person's personality and interests from their Pinterest boards
- correct_answer and wrong_answers must be specific facts from their board names/descriptions
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: exact board name or pin description (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {name} from their Pinterest profile:\n{raw_content[:8000]}"

    try:
        import httpx
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
                headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = re.sub(r"^```json\s*", "", content.strip())
            content = re.sub(r"\s*```$", "", content.strip())
            questions = json.loads(content)
            return questions
    except Exception as e:
        return []