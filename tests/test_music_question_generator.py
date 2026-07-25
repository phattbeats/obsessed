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

PHA-1506 nit: the parser-level slice must preserve category diversity from
the LLM response. If the LLM ever drifts into "all entertainment" (because
the prompt's "Mix categories evenly" rule is loosened), the parser must
not paper over it — the regression has to be visible in tests.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scraper.reddit import CATEGORIES, generate_questions


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


class TestMusicQuestionGeneratorCategoryBalance:
    """PHA-1506: parser-level pass-through preserves category diversity.

    The system prompt asks the LLM to "Mix categories evenly across the 6
    categories," but the parser does not enforce category balance — it just
    JSON-parses whatever the LLM emitted. If the LLM ever drifts into a
    monoculture response (e.g. all entertainment), the parser must surface
    that as-is, not silently rebalance. These tests guard both halves of
    that contract: diversity in → diversity out, monoculture in → monoculture out.
    """

    # Threshold from the issue: ≥3 distinct categories across a 50-question
    # response. The actual model is asked for 6, but we don't want the test
    # to demand perfect coverage — partial diversity is acceptable.
    MIN_DISTINCT_CATEGORIES = 3

    @staticmethod
    def _build_question(index: int, category: str) -> dict:
        """Build a single question dict at the given index with the given category."""
        return {
            "category": category,
            "question_text": f"Question {index} about {category}?",
            "correct_answer": f"answer_{index}",
            "wrong_answers": [f"wrong_a_{index}", f"wrong_b_{index}", f"wrong_c_{index}"],
            "difficulty": 1,
            "source_snippet": f"snippet about {category} #{index}",
        }

    @classmethod
    def _fixture_json(cls, categories: list[str], total: int = 50) -> str:
        """Build a 50-question JSON fixture spanning the requested categories.

        Distributes questions as evenly as possible across `categories`,
        with the remainder added to the first entries. Result is a single
        JSON-encoded list — exactly the shape the LLM is expected to return.
        """
        if not categories:
            raise ValueError("categories must be non-empty")
        questions = []
        per_cat = total // len(categories)
        remainder = total % len(categories)
        for i, cat in enumerate(categories):
            count = per_cat + (1 if i < remainder else 0)
            for j in range(count):
                questions.append(cls._build_question(len(questions) + 1, cat))
        # Sanity check the count — if categories list is wrong, the test
        # should fail loudly, not silently produce a wrong-size fixture.
        assert len(questions) == total, (
            f"Fixture build bug: expected {total} questions, got {len(questions)} "
            f"for categories={categories}"
        )
        return json.dumps(questions)

    @pytest.mark.asyncio
    async def test_balanced_response_preserves_category_diversity(self):
        """A balanced 50-question response (all 6 categories) → parser returns all 6."""
        assert len(CATEGORIES) == 6, (
            f"Expected 6 categories in app.services.scraper.reddit.CATEGORIES, "
            f"got {len(CATEGORIES)}: {CATEGORIES}. Update this test if the schema changed."
        )
        fixture = self._fixture_json(CATEGORIES)
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured, response_text=fixture),
        ):
            result = await generate_questions(
                profile_id=1, raw_content=LASTFM_RAW, name="Richard Jones"
            )

        assert len(result) == 50
        distinct = {q["category"] for q in result}
        assert len(distinct) >= self.MIN_DISTINCT_CATEGORIES, (
            f"Expected parser-level pass-through to preserve at least "
            f"{self.MIN_DISTINCT_CATEGORIES} distinct categories from the LLM response, "
            f"got {sorted(distinct)} ({len(distinct)} distinct)."
        )
        assert distinct == set(CATEGORIES), (
            f"All categories from the balanced fixture should appear in the parsed "
            f"result. Expected {sorted(CATEGORIES)}, got {sorted(distinct)}."
        )

    @pytest.mark.asyncio
    async def test_minimum_diversity_response_passes_threshold(self):
        """A 50-question response across exactly 3 categories → meets the ≥3 threshold."""
        three_categories = ["entertainment", "art_literature", "sports"]
        fixture = self._fixture_json(three_categories)
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured, response_text=fixture),
        ):
            result = await generate_questions(
                profile_id=1, raw_content=LASTFM_RAW, name="Richard Jones"
            )

        assert len(result) == 50
        distinct = {q["category"] for q in result}
        assert len(distinct) == self.MIN_DISTINCT_CATEGORIES
        assert distinct == set(three_categories)

    @pytest.mark.asyncio
    async def test_monoculture_response_passes_through_unchanged(self):
        """All-entertainment response → parser returns all entertainment (no silent rebalancing).

        This is the inverse of the diversity test. If the parser ever grew a
        "rebalance toward diversity" feature, this test would fail — and that
        failure is the point. The prompt is the right place to enforce
        diversity; the parser should faithfully reflect what the LLM emitted.
        """
        fixture = self._fixture_json(["entertainment"])
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured, response_text=fixture),
        ):
            result = await generate_questions(
                profile_id=1, raw_content=LASTFM_RAW, name="Richard Jones"
            )

        assert len(result) == 50
        distinct = {q["category"] for q in result}
        assert distinct == {"entertainment"}, (
            f"Parser must pass through categories as-is — no invisible dedup or "
            f"rebalancing. Got {sorted(distinct)}."
        )
        # All 50 questions should be entertainment — explicitly assert the
        # count to catch any partial filtering logic.
        assert sum(1 for q in result if q["category"] == "entertainment") == 50

    @pytest.mark.asyncio
    async def test_existing_good_questions_fixture_fails_diversity_threshold(self):
        """The current GOOD_QUESTIONS_JSON (entertainment + art_literature × 25) is itself
        a 2-category fixture. This test pins that as a known limitation so a future
        promotion of GOOD_QUESTIONS_JSON to a more diverse shape is visible.

        If this test ever fails, GOOD_QUESTIONS_JSON has been updated to span ≥3
        categories — which is a good thing, but the change should be conscious.
        """
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),  # uses module-level GOOD_QUESTIONS_JSON
        ):
            result = await generate_questions(
                profile_id=1, raw_content=LASTFM_RAW, name="Richard Jones"
            )

        assert len(result) == 50
        distinct = {q["category"] for q in result}
        # Pin the current 2-category shape as a known limitation.
        assert len(distinct) == 2, (
            f"GOOD_QUESTIONS_JSON used to span exactly 2 categories (entertainment + "
            f"art_literature). If this assertion now fails, the fixture has been "
            f"improved — update this test to assert >= {self.MIN_DISTINCT_CATEGORIES} "
            f"distinct categories instead. Current distinct categories: {sorted(distinct)}."
        )
        assert distinct == {"entertainment", "art_literature"}