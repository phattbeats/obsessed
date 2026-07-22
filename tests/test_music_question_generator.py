"""
PHA-1344: music-taste question-generator tests.

Validates the "guess their obsession" path: when last.fm-shaped raw_content
is in the input blob, the shared reddit.generate_questions must (a) inject
the last.fm domain hint into the system prompt, (b) request a full 50-
question budget (not the 25 fallback for thin content), and (c) parse the
LLM response into the standard question shape.

Fixture-driven — mocks the LiteLLM /chat/completions endpoint with httpx
so no live API key is needed in CI. The captured system_prompt is asserted
on for the last.fm hint marker, and the user_prompt is asserted on for the
last.fm raw_content so we know the actual blob reaches the model.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scraper.reddit import generate_questions


# ─────────────────────────────────────────────────────────────────
# Sample last.fm raw_content — same shape scrape_lastfm produces.
# Real fixture data from tests/fixtures/lastfm/sample_user.json would be
# even better, but a hand-rolled snippet keeps the test self-contained
# and removes the dependency on the scraper module's _build_raw_content.
# ─────────────────────────────────────────────────────────────────
LASTFM_RAW = """[last.fm profile] rj
  Real name: Richard Jones
  Total scrobbles: 50234
  Country: United Kingdom
  Registered since: 2005

[Top artists — all-time]
  Radiohead — 512 plays
  Boards of Canada — 301 plays
  Aphex Twin — 289 plays

[Top tracks — all-time]
  Everything In Its Right Place by Radiohead — 45 plays
  Roygbiv by Boards of Canada — 38 plays

[Top albums — all-time]
  Kid A by Radiohead — 60 plays
  Music Has the Right to Children by Boards of Canada — 55 plays

[Recent scrobbles]
  Idioteque by Radiohead (now playing)
  Pyramid Song by Radiohead
"""

# A plausible 50-question response. Structure mirrors what the shared
# generator expects: list of dicts with category, question_text,
# correct_answer, wrong_answers, difficulty, source_snippet.
GOOD_QUESTIONS_JSON = json.dumps([
    {
        "category": "entertainment",
        "question_text": "Which artist is Richard Jones's most-played of all time?",
        "correct_answer": "Radiohead",
        "wrong_answers": ["Boards of Canada", "Aphex Twin", "Massive Attack"],
        "difficulty": 1,
        "source_snippet": "Radiohead — 512 plays",
    },
    {
        "category": "art_literature",
        "question_text": "Which Radiohead album dominates Richard Jones's top albums?",
        "correct_answer": "Kid A",
        "wrong_answers": ["OK Computer", "In Rainbows", "Amnesiac"],
        "difficulty": 2,
        "source_snippet": "Kid A by Radiohead — 60 plays",
    },
] * 25)  # pad to 50 entries so the budget=50 logic has data to slice


def _make_mock_client(captured: dict, response_text: str = GOOD_QUESTIONS_JSON) -> MagicMock:
    """Return an httpx.AsyncClient stand-in that captures the request body and returns the canned response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": response_text}}],
    }
    mock_resp.raise_for_status = MagicMock()

    async def _capture_post(url, json=None, headers=None):
        captured["url"] = url
        captured["json"] = json or {}
        captured["headers"] = headers or {}
        return mock_resp

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=_capture_post)
    return mock_client


class TestMusicQuestionGeneratorLastfmPath:
    """The core PHA-1344 contract: last.fm raw_content → obsession questions."""

    @pytest.mark.asyncio
    async def test_lastfm_content_triggers_music_domain_hint(self):
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            result = await generate_questions(
                profile_id=1, raw_content=LASTFM_RAW, name="Richard Jones"
            )

        system_prompt = captured["json"]["messages"][0]["content"]
        assert "last.fm" in system_prompt.lower(), (
            "last.fm domain hint must be injected into the system prompt "
            "when [last.fm profile] marker is present in raw_content."
        )
        assert "top artists" in system_prompt.lower(), (
            "music hint must steer the model toward favorite-artist questions."
        )
        assert "obsession" in system_prompt.lower() or "listening history" in system_prompt.lower(), (
            "music hint must frame questions around listening history / obsession."
        )

    @pytest.mark.asyncio
    async def test_lastfm_content_uses_full_50_question_budget(self):
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            await generate_questions(
                profile_id=1, raw_content=LASTFM_RAW, name="Richard Jones"
            )

        system_prompt = captured["json"]["messages"][0]["content"]
        # The model spec embeds the count into the prompt as "generate exactly N".
        assert "exactly 50" in system_prompt, (
            "last.fm content is rich (>500 chars) and must trigger the 50-question "
            "budget, not the 25-question thin-content fallback."
        )

    @pytest.mark.asyncio
    async def test_lastfm_raw_content_reaches_model_in_user_prompt(self):
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            await generate_questions(
                profile_id=1, raw_content=LASTFM_RAW, name="Richard Jones"
            )

        user_prompt = captured["json"]["messages"][1]["content"]
        # The user prompt should contain the last.fm raw content so the model can
        # actually answer questions about it. Look for distinctive facts.
        assert "Radiohead" in user_prompt
        assert "Boards of Canada" in user_prompt
        assert "Kid A" in user_prompt
        assert "Richard Jones" in user_prompt

    @pytest.mark.asyncio
    async def test_lastfm_response_parses_into_questions(self):
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            result = await generate_questions(
                profile_id=1, raw_content=LASTFM_RAW, name="Richard Jones"
            )

        assert isinstance(result, list)
        assert len(result) == 50
        for q in result[:3]:  # spot-check structure
            assert {"category", "question_text", "correct_answer", "wrong_answers"} <= set(q)


class TestMusicQuestionGeneratorMixedContent:
    """When last.fm data arrives alongside other sources, all hints must fire."""

    @pytest.mark.asyncio
    async def test_mixed_lastfm_and_news_content_includes_both_hints(self):
        mixed = (
            LASTFM_RAW
            + "\n\n[News results for: Richard Jones]\n  - Concert review in NME\n"
        )
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            await generate_questions(
                profile_id=1, raw_content=mixed, name="Richard Jones"
            )

        system_prompt = captured["json"]["messages"][0]["content"]
        assert "last.fm" in system_prompt.lower()
        assert "news" in system_prompt.lower()


class TestMusicQuestionGeneratorNegativeCases:
    """No music data → no music hint. Thin content → 25-question budget."""

    @pytest.mark.asyncio
    async def test_no_music_content_omits_music_hint(self):
        non_music_raw = (
            "[Reddit search] rj posted about Python testing patterns on r/programming.\n"
            "[Reddit search] rj commented on a thread about ML evaluation metrics.\n"
        )
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            await generate_questions(
                profile_id=1, raw_content=non_music_raw, name="rj"
            )

        system_prompt = captured["json"]["messages"][0]["content"]
        assert "last.fm" not in system_prompt.lower()
        assert "listening history" not in system_prompt.lower()
        # Thin content triggers the 25-question budget.
        assert "exactly 25" in system_prompt

    @pytest.mark.asyncio
    async def test_empty_raw_content_returns_empty(self):
        # No LLM call should be made for empty input.
        result = await generate_questions(
            profile_id=1, raw_content="", name="nobody"
        )
        assert result == []