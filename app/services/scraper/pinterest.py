"""
Pinterest scraper for obsessed pipeline.
Failover chain: pinterest-dl (primary) → pinscrape (fallback) → crawl4ai (final).
Keep scrape_pinterest(handle) and generate_questions(...) signatures unchanged.
"""
import json, re, subprocess, tempfile, os
from typing import Optional
from app.config import settings

CRAWL4AI_URL = "http://crawl4ai:11235"

# ---------------------------------------------------------------------------
# Node.js tool helpers
# ---------------------------------------------------------------------------


def _run_node(cmd: list[str], timeout: int = 30) -> str:
    """Run a Node.js CLI tool, return stdout or raise."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "NODE_PATH": os.environ.get("NODE_PATH", "")},
    )
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} exited {result.returncode}: {result.stderr[:200]}")
    return result.stdout


async def _scrape_pinterest_dl(handle: str) -> tuple[str, list[dict]]:
    """
    Primary: pinterest-dl (npm). Returns (formatted_text, boards_list).
    Boards: [{name, url, pin_count}].
    """
    handle = handle.strip().lstrip("@")
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = os.path.join(tmpdir, "result.json")
        try:
            # pinterest-dl writes to stdout as JSON Lines or a JSON file
            # Use --output to write a specific file
            _run_node(
                ["node", "-e",
                 f"const pdl = require('pinterest-dl'); pdl.download('{handle}', '{tmpdir}').then(r => require('fs').writeFileSync('{out_file}', JSON.stringify(r, null, 2)))",
                 ],
                timeout=40,
            )
        except Exception as e:
            raise RuntimeError(f"pinterest-dl failed: {e}")

        if not os.path.exists(out_file):
            raise RuntimeError("pinterest-dl did not produce output file")

        data = json.load(open(out_file))

    # data shape: { profile: {name, handle, boards: [{name, url, pin_count}]} }
    profile = data.get("profile", {})
    boards = profile.get("boards", [])
    clean_boards = [
        {"name": b.get("name", ""), "url": b.get("url", ""), "pin_count": str(b.get("pin_count", ""))}
        for b in boards if b.get("name")
    ]

    lines = [f"[Pinterest profile: {profile.get('name') or handle}]"]
    lines.append(f"Profile: {profile.get('name') or handle} (https://www.pinterest.com/{handle}/)")
    if clean_boards:
        lines.append(f"Interest boards ({len(clean_boards)}):")
        for b in clean_boards:
            pc = f" ({b['pin_count']} Pins)" if b["pin_count"] else ""
            lines.append(f"  - {b['name']}{pc} — {b['url']}")
    else:
        lines.append("(No boards found)")

    return "\n".join(lines), clean_boards


async def _scrape_pinscrape(handle: str) -> tuple[str, list[dict]]:
    """
    Secondary: pinscrape (npm). Returns (formatted_text, boards_list).
    """
    handle = handle.strip().lstrip("@")
    try:
        raw = _run_node(
            ["npx", "-y", "pinscrape", handle],
            timeout=40,
        )
    except Exception as e:
        raise RuntimeError(f"pinscrape failed: {e}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # pinscrape sometimes emits plain text or non-JSON; fall through
        raise RuntimeError(f"pinscrape returned non-JSON: {raw[:200]}")

    # data shape from pinscrape: { username, profile: {name}, boards: [] }
    username = data.get("username", handle)
    profile_name = data.get("profile", {}).get("name", username)
    boards_raw = data.get("boards", []) or data.get("data", [])

    clean_boards = []
    for b in boards_raw:
        if isinstance(b, dict):
            clean_boards.append({
                "name": b.get("name", b.get("title", "")),
                "url": b.get("url", ""),
                "pin_count": str(b.get("pin_count", b.get("pins", ""))),
            })
        elif isinstance(b, str):
            clean_boards.append({"name": b, "url": "", "pin_count": ""})

    lines = [f"[Pinterest profile: {profile_name or username}]"]
    lines.append(f"Profile: {profile_name or username} (https://www.pinterest.com/{username}/)")
    if clean_boards:
        lines.append(f"Interest boards ({len(clean_boards)}):")
        for b in clean_boards:
            pc = f" ({b['pin_count']} Pins)" if b["pin_count"] else ""
            lines.append(f"  - {b['name']}{pc} — {b['url']}")
    else:
        lines.append("(No boards found)")

    return "\n".join(lines), clean_boards


async def _scrape_pinterest_crawl4ai(handle: str) -> tuple[str, list[dict]]:
    """
    Final fallback: crawl4ai against pinterest.com/{handle}.
    Duplicates the old single-source logic.
    """
    profile_url = f"https://www.pinterest.com/{handle.strip().lstrip('@')}/"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                CRAWL4AI_URL + "/crawl",
                json={
                    "urls": [profile_url],
                    "headless": True,
                    "wait_for": ".userProfilePage",
                    "page_timeout": 30000,
                },
                headers={"Authorization": f"Bearer {settings.crawl4ai_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return "", []
            raw_md = results[0].get("markdown", {})
            if isinstance(raw_md, dict):
                raw_md = raw_md.get("raw_markdown", "") or raw_md.get("markdown_with_citations", "")
    except Exception as e:
        return f"[Pinterest scrape error: {e}]", []

    # Parse profile name
    profile_name = ""
    for line in raw_md.split("\n"):
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            profile_name = m.group(1).strip()
            break

    # Parse boards from markdown
    board_pattern = re.compile(
        r"\[([^\]]+)\]\((https://www\.pinterest\.com/[^)]+)\)[^\[]*(?:(\d+)\s*Pins)",
        re.IGNORECASE,
    )
    boards = []
    for m in board_pattern.finditer(raw_md):
        boards.append({
            "name": m.group(1).strip(),
            "url": m.group(2),
            "pin_count": m.group(3) or "",
        })

    seen = set()
    unique_boards = []
    for b in boards:
        if b["name"].lower() not in seen:
            seen.add(b["name"].lower())
            unique_boards.append(b)

    lines_out = [f"[Pinterest profile: {profile_name or handle}]"]
    lines_out.append(f"Profile: {profile_name or handle} ({profile_url})")
    if unique_boards:
        lines_out.append(f"Interest boards ({len(unique_boards)}):")
        for b in unique_boards:
            pc = f" ({b['pin_count']} Pins)" if b["pin_count"] else ""
            lines_out.append(f"  - {b['name']}{pc} — {b['url']}")
    else:
        lines_out.append("(No boards visible — profile may be private)")

    return "\n".join(lines_out), unique_boards


# ---------------------------------------------------------------------------
# Public API — unchanged signature
# ---------------------------------------------------------------------------

async def scrape_pinterest(handle: str) -> tuple[str, list[dict]]:
    """
    Three-source failover scraper for Pinterest profiles.

    Try order:
      1. pinterest-dl (npm, no auth, profile + boards via API)
      2. pinscrape  (npm, fallback)
      3. crawl4ai   (final fallback, preserves old behavior)

    Returns (raw_text, boards_list) where boards entries are:
      {name: str, url: str, pin_count: str}

    On all failures: returns ("[Pinterest scrape error: all sources failed]", [])
    """
    handle = handle.strip().lstrip("@")
    last_err = ""

    for label, fn in [
        ("pinterest-dl", _scrape_pinterest_dl),
        ("pinscrape",   _scrape_pinscrape),
        ("crawl4ai",    _scrape_pinterest_crawl4ai),
    ]:
        try:
            text, boards = await fn(handle)
            # Accept non-empty text as success (pinterest-dl/pinscrape may return
            # empty boards if profile is private — still valid output)
            if text and not text.startswith("[Pinterest scrape error:"):
                return text, boards
        except Exception as exc:
            last_err = f"{label}: {exc}"
            continue

    return (
        f"[Pinterest scrape error: all sources failed for @{handle}"
        f" (last: {last_err})]",
        [],
    )


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

    user_prompt = f"Facts about {name} from their Pinterest profile:\n{raw_content[: settings.content_max_chars]}"

    try:
        import httpx, os
        api_key = settings.litellm_api_key or os.environ.get("LITELLM_API_KEY", "")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.litellm_base}/chat/completions",
                json={
                    "model": settings.litellm_model,
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