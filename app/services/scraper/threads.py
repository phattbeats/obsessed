import httpx, json, re
from typing import Optional

FLARESOLVERR_URL = "http://10.0.0.100:8191/v1"
LITELLM_BASE = "http://10.0.0.100:4000"
LITELLM_API_KEY = "sk-vantage"


async def scrape_threads(handle: str) -> tuple[str, dict]:
    """
    Scrape a public Threads.net profile via FlareSolverr.
    Returns (raw_text, profile_data_dict).
    profile_data: {username, display_name, follower_count, thread_count, bio}
    """
    url = f"https://threads.net/@{handle}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                FLARESOLVERR_URL,
                json={
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": 45000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return f"[Threads scrape error: {e}]", {}

    if data.get("status") != "ok":
        return f"[Threads FlareSolverr error: {data.get('message', 'unknown')}]", {}

    # HTML comes in solution.response (string), not solution.html
    html = data.get("solution", {}).get("response", "")

    profile = {"username": handle, "display_name": "", "follower_count": "", "thread_count": "", "bio": ""}

    # Extract og:description — format: "{N} Followers • {N} Threads • {bio text}. See the latest..."
    og_match = re.search(
        r'<meta[^>]*(?:property|name)=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']',
        html,
    )
    if not og_match:
        og_match = re.search(r'content=["\']([^"\']*Followers[^"\']*)["\']', html)

    if og_match:
        desc = og_match.group(1)
        profile["bio"] = desc

        # Parse: "5.5M Followers • 143 Threads • Bio text. See the latest..."
        # Strip trailing "See the latest..." clause
        desc = re.sub(r'\. See the latest.*$', '', desc)

        followers_match = re.match(r'([\d.,]+[KM]?)\s*Followers', desc)
        threads_match = re.search(r'([\d,]+)\s*Threads', desc)

        if followers_match:
            profile["follower_count"] = followers_match.group(1)
        if threads_match:
            profile["thread_count"] = threads_match.group(1)

        # Bio is everything after the last "•"
        parts = desc.split('•')
        if len(parts) >= 3:
            profile["bio"] = parts[-1].strip()

    # Extract display name from <title>
    title_match = re.search(r'<title>([^<]+)</title>', html)
    if title_match:
        title = title_match.group(1)
        # Format: "Name (@handle) • Threads, Say more"
        m = re.match(r'^(.+?)\s*\(@', title)
        if m:
            profile["display_name"] = m.group(1).strip()

    # Build readable text
    lines = [f"[Threads profile: @{profile['username']}]"]
    lines.append(f"Profile: {profile['display_name'] or profile['username']} (@{profile['username']})")
    parts_info = []
    if profile["follower_count"]:
        parts_info.append(f"{profile['follower_count']} Followers")
    if profile["thread_count"]:
        parts_info.append(f"{profile['thread_count']} Threads")
    if parts_info:
        lines.append(" · ".join(parts_info) + " · " + profile["bio"])
    elif profile["bio"]:
        lines.append(profile["bio"])

    return "\n".join(lines), profile


async def generate_questions(profile_id: int, raw_content: str, name: str) -> list[dict]:
    """Generate trivia questions from Threads profile content via LiteLLM."""
    if not raw_content.strip():
        return []

    system_prompt = f"""You are a trivia question generator. Given facts about a person named "{name}", generate exactly 25 trivia questions about their personality and interests based on their Threads.net profile data.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- Questions are about what you can infer about the person from their Threads bio and social metrics
- correct_answer and wrong_answers must be specific facts from the profile
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard
- Mix categories evenly across the 6 categories
- source_snippet: exact phrase from the bio or profile (max 20 words)
- Return ONLY the JSON array, no commentary"""

    user_prompt = f"Facts about {name} from their Threads.net profile:\n{raw_content[:6000]}"

    try:
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
