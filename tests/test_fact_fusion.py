"""
PHA-1510: fact-fusion multi-hop question generator tests.

Mirrors the mocking pattern established in tests/test_music_question_generator.py —
mocks the LiteLLM /chat/completions endpoint via httpx.AsyncClient so no live API
key/network is needed in CI.

Covers:
  - extract_facts(): one batched call, facts tagged by source_index/label/type.
  - match_pairs(): pure-python cross-source ranking (no LLM), interesting-pairs
    weight map as a tiebreaker (not a hard filter).
  - generate_fusion_questions(): one batched call, output shape matches
    reddit.generate_questions.
  - run_fact_fusion(): end-to-end orchestration on a 3-source fixture, plus the
    "safe to call with insufficient overlap" contract.
  - A replay/regression check on the actual wiring in
    app.routes.profiles._generate_questions_async: fusion must only ADD
    questions on top of the single-source set, never reduce it.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.fact_fusion import (
    extract_facts,
    generate_fusion_questions,
    match_pairs,
    run_fact_fusion,
    split_raw_parts,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "fact_fusion" / "three_source_raw.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text())
RAW_PARTS = FIXTURE["raw_parts"]
NAME = FIXTURE["name"]


def _make_mock_client(captured_calls: list, responses: list[str]) -> MagicMock:
    """httpx.AsyncClient stand-in. Each POST call pops the next canned response
    off `responses` and appends the captured request body to `captured_calls`,
    so a test can assert on N sequential LLM calls (extract, then generate).
    """
    call_index = {"n": 0}

    async def _capture_post(url, json=None, headers=None):
        captured_calls.append({"url": url, "json": json or {}, "headers": headers or {}})
        idx = min(call_index["n"], len(responses) - 1)
        call_index["n"] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": responses[idx]}}]}
        resp.raise_for_status = MagicMock()
        return resp

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=_capture_post)
    return mock_client


FACTS_RESPONSE = json.dumps([
    {
        "source_index": 0,
        "source_label": "News results for: Jordan Ellis",
        "fact_text": "Jordan Ellis was honored by the Delaware County Chamber of Commerce for founding Acme Consulting LLC in 2015.",
        "entities": ["Jordan Ellis", "Acme Consulting LLC", "Delaware County"],
    },
    {
        "source_index": 1,
        "source_label": "Court docket for: Jordan Ellis",
        "fact_text": "In 2019, Jordan Ellis was party to a civil case (DC-2019-00456) involving Acme Consulting LLC that was settled.",
        "entities": ["Jordan Ellis", "Acme Consulting LLC", "2019"],
    },
    {
        "source_index": 2,
        "source_label": "Reddit r/smallbusiness",
        "fact_text": "Jordan Ellis posted on Reddit about running Acme Consulting LLC and a rough 2019 lawsuit.",
        "entities": ["Jordan Ellis", "Acme Consulting LLC", "2019"],
    },
])

FUSION_QUESTIONS_RESPONSE = json.dumps([
    {
        "category": "history",
        "question_text": (
            "The person honored by the Delaware County Chamber of Commerce for founding "
            "Acme Consulting LLC was also party to which 2019 civil case?"
        ),
        "correct_answer": "Case DC-2019-00456",
        "wrong_answers": ["Case DC-2020-01123", "Case DC-2018-00099", "Case DC-2019-00789"],
        "difficulty": 3,
        "source_snippet": "News + Court",
    },
])


class TestExtractFacts:
    @pytest.mark.asyncio
    async def test_tags_facts_by_source_and_sends_all_blocks_in_one_call(self):
        captured: list = []
        with patch(
            "app.services.fact_fusion.httpx.AsyncClient",
            return_value=_make_mock_client(captured, [FACTS_RESPONSE]),
        ):
            facts = await extract_facts(RAW_PARTS)

        # Exactly one batched call, not one per source block.
        assert len(captured) == 1

        user_prompt = captured[0]["json"]["messages"][1]["content"]
        assert "### Source 0: News results for: Jordan Ellis" in user_prompt
        assert "### Source 1: Court docket for: Jordan Ellis" in user_prompt
        assert "### Source 2: Reddit r/smallbusiness" in user_prompt

        assert len(facts) == 3
        by_idx = {f["source_index"]: f for f in facts}
        assert by_idx[0]["source_type"] == "news"
        assert by_idx[1]["source_type"] == "court"
        assert by_idx[2]["source_type"] == "reddit"
        assert "Acme Consulting LLC" in by_idx[0]["entities"]
        assert all("fact_text" in f and f["fact_text"] for f in facts)

    @pytest.mark.asyncio
    async def test_fewer_than_two_sources_skips_the_llm_call(self):
        captured: list = []
        with patch(
            "app.services.fact_fusion.httpx.AsyncClient",
            return_value=_make_mock_client(captured, [FACTS_RESPONSE]),
        ):
            facts = await extract_facts([RAW_PARTS[0]])

        assert facts == []
        assert captured == []  # no LLM call attempted — nothing to bridge

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        assert await extract_facts([]) == []
        assert await extract_facts(["", "   "]) == []

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty_not_raise(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("connection refused"))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("app.services.fact_fusion.httpx.AsyncClient", return_value=mock_client):
            facts = await extract_facts(RAW_PARTS)
        assert facts == []


class TestMatchPairs:
    def _facts(self):
        return [
            {"source_index": 0, "source_label": "News results for: Jordan Ellis",
             "source_type": "news", "fact_text": "news fact",
             "entities": ["Acme Consulting LLC"]},
            {"source_index": 1, "source_label": "Court docket for: Jordan Ellis",
             "source_type": "court", "fact_text": "court fact",
             "entities": ["Acme Consulting LLC"]},
            {"source_index": 2, "source_label": "Reddit r/smallbusiness",
             "source_type": "reddit", "fact_text": "reddit fact",
             "entities": ["Acme Consulting LLC"]},
            # An entity that only appears in a single source — must never pair.
            {"source_index": 0, "source_label": "News results for: Jordan Ellis",
             "source_type": "news", "fact_text": "news-only fact",
             "entities": ["Only In News Ever"]},
        ]

    def test_pairs_require_two_distinct_sources(self):
        pairs = match_pairs(self._facts())
        entities = {p["entity"] for p in pairs}
        assert "only in news ever" not in entities

    def test_no_llm_involved(self):
        """Pure python — must not import/require httpx to run."""
        pairs = match_pairs(self._facts())
        assert isinstance(pairs, list)

    def test_generates_all_cross_source_combinations(self):
        pairs = match_pairs(self._facts())
        combos = {frozenset({p["fact_a"]["source_type"], p["fact_b"]["source_type"]}) for p in pairs}
        assert frozenset({"news", "court"}) in combos
        assert frozenset({"news", "reddit"}) in combos
        assert frozenset({"court", "reddit"}) in combos

    def test_interesting_pairs_weight_map_biases_ranking_not_filters(self):
        pairs = match_pairs(self._facts())
        # news+court is in the weight map (boosted); news+reddit and court+reddit are not.
        # All three are still present ("boost, not filter") but news+court ranks first.
        assert pairs, "expected at least one pair"
        top = pairs[0]
        assert frozenset({top["fact_a"]["source_type"], top["fact_b"]["source_type"]}) == frozenset({"news", "court"})
        assert top["weight"] > 0
        low_weight_pairs = [p for p in pairs if p["weight"] == 0]
        assert low_weight_pairs, "unweighted combos must still be eligible, not filtered out"

    def test_empty_facts_returns_empty(self):
        assert match_pairs([]) == []

    def test_source_count_is_always_at_least_two(self):
        for p in match_pairs(self._facts()):
            assert p["source_count"] >= 2


class TestGenerateFusionQuestions:
    @pytest.mark.asyncio
    async def test_batches_all_pairs_into_one_call(self):
        facts = [
            {"source_index": 0, "source_label": "News results for: Jordan Ellis",
             "source_type": "news", "fact_text": "Jordan Ellis founded Acme Consulting LLC.",
             "entities": ["Acme Consulting LLC"]},
            {"source_index": 1, "source_label": "Court docket for: Jordan Ellis",
             "source_type": "court", "fact_text": "Acme Consulting LLC was party to case DC-2019-00456.",
             "entities": ["Acme Consulting LLC"]},
        ]
        pairs = match_pairs(facts)
        assert len(pairs) == 1

        captured: list = []
        with patch(
            "app.services.fact_fusion.httpx.AsyncClient",
            return_value=_make_mock_client(captured, [FUSION_QUESTIONS_RESPONSE]),
        ):
            questions = await generate_fusion_questions(1, NAME, pairs)

        assert len(captured) == 1  # one batched call for all pairs
        user_prompt = captured[0]["json"]["messages"][1]["content"]
        assert "Pair 0" in user_prompt
        assert "Acme Consulting LLC" in user_prompt
        assert "Jordan Ellis founded Acme Consulting LLC." in user_prompt
        assert "Acme Consulting LLC was party to case DC-2019-00456." in user_prompt

        assert len(questions) == 1
        q = questions[0]
        assert {"category", "question_text", "correct_answer", "wrong_answers", "difficulty", "source_snippet"} <= set(q)
        assert q["is_fusion"] is True
        assert len(q["wrong_answers"]) == 3

    @pytest.mark.asyncio
    async def test_no_pairs_skips_the_llm_call(self):
        captured: list = []
        with patch(
            "app.services.fact_fusion.httpx.AsyncClient",
            return_value=_make_mock_client(captured, [FUSION_QUESTIONS_RESPONSE]),
        ):
            questions = await generate_fusion_questions(1, NAME, [])
        assert questions == []
        assert captured == []

    @pytest.mark.asyncio
    async def test_malformed_llm_output_returns_empty_not_raise(self):
        pairs = match_pairs([
            {"source_index": 0, "source_label": "A", "source_type": "news",
             "fact_text": "fact a", "entities": ["Shared Entity"]},
            {"source_index": 1, "source_label": "B", "source_type": "court",
             "fact_text": "fact b", "entities": ["Shared Entity"]},
        ])
        captured: list = []
        with patch(
            "app.services.fact_fusion.httpx.AsyncClient",
            return_value=_make_mock_client(captured, ["not json at all, sorry"]),
        ):
            questions = await generate_fusion_questions(1, NAME, pairs)
        assert questions == []


class TestSplitRawParts:
    def test_resplits_joined_raw_content_on_bracket_labels(self):
        joined = "\n".join(RAW_PARTS)
        parts = split_raw_parts(joined)
        assert len(parts) == 3
        assert parts[0].startswith("[News results for: Jordan Ellis]")
        assert parts[1].startswith("[Court docket for: Jordan Ellis]")
        assert parts[2].startswith("[Reddit r/smallbusiness]")

    def test_empty_input_returns_empty(self):
        assert split_raw_parts("") == []
        assert split_raw_parts("   ") == []

    def test_content_with_no_bracket_labels_is_returned_as_a_single_block(self):
        assert split_raw_parts("just some plain text, no labels here") == [
            "just some plain text, no labels here"
        ]


class TestRunFactFusionEndToEnd:
    @pytest.mark.asyncio
    async def test_three_source_fixture_produces_fusion_questions(self):
        captured: list = []
        with patch(
            "app.services.fact_fusion.httpx.AsyncClient",
            return_value=_make_mock_client(captured, [FACTS_RESPONSE, FUSION_QUESTIONS_RESPONSE]),
        ):
            questions = await run_fact_fusion(profile_id=1, raw_parts=RAW_PARTS, name=NAME)

        # extract_facts (1 call) + generate_fusion_questions (1 call) == 2 total,
        # matching the PHA-1510 cost decision exactly.
        assert len(captured) == 2
        assert questions, "expected at least one multi-hop question from the 3-source fixture"
        for q in questions:
            assert q["is_fusion"] is True
            assert q["question_text"]
            assert len(q["wrong_answers"]) == 3

    @pytest.mark.asyncio
    async def test_insufficient_source_overlap_returns_empty_gracefully(self):
        """A single source has nothing to bridge — must return [] without raising or
        making any LLM call at all (not even the first extract_facts call)."""
        captured: list = []
        with patch(
            "app.services.fact_fusion.httpx.AsyncClient",
            return_value=_make_mock_client(captured, [FACTS_RESPONSE, FUSION_QUESTIONS_RESPONSE]),
        ):
            questions = await run_fact_fusion(profile_id=1, raw_parts=[RAW_PARTS[0]], name=NAME)
        assert questions == []
        assert captured == []

    @pytest.mark.asyncio
    async def test_extract_succeeds_but_no_bridgeable_pairs_returns_empty(self):
        """extract_facts succeeds but the facts share no entity across sources —
        generate_fusion_questions must never be called."""
        no_overlap_facts = json.dumps([
            {"source_index": 0, "source_label": "News results for: X", "fact_text": "fact a", "entities": ["Only Here"]},
            {"source_index": 1, "source_label": "Court docket for: X", "fact_text": "fact b", "entities": ["Only There"]},
        ])
        captured: list = []
        with patch(
            "app.services.fact_fusion.httpx.AsyncClient",
            return_value=_make_mock_client(captured, [no_overlap_facts, FUSION_QUESTIONS_RESPONSE]),
        ):
            questions = await run_fact_fusion(profile_id=1, raw_parts=RAW_PARTS[:2], name=NAME)
        assert questions == []
        assert len(captured) == 1  # extract_facts ran, generate_fusion_questions did not

    @pytest.mark.asyncio
    async def test_never_raises_on_llm_failure(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("app.services.fact_fusion.httpx.AsyncClient", return_value=mock_client):
            questions = await run_fact_fusion(profile_id=1, raw_parts=RAW_PARTS, name=NAME)
        assert questions == []


# ─────────────────────────────────────────────────────────────────────────
# Replay/regression check against the actual wiring in
# app.routes.profiles._generate_questions_async — fusion must only ADD
# questions on top of the single-source set, never reduce it.
# ─────────────────────────────────────────────────────────────────────────

def _single_source_question(n: int) -> dict:
    return {
        "category": "history",
        "question_text": f"Single-source question {n} about Jordan Ellis?",
        "correct_answer": f"answer {n}",
        "wrong_answers": [f"w{n}a", f"w{n}b", f"w{n}c"],
        "difficulty": 1,
        "source_snippet": "single-source",
    }


def _fusion_question(n: int) -> dict:
    return {
        "category": "history",
        "question_text": f"Multi-hop fusion question {n} bridging News and Court about Jordan Ellis?",
        "correct_answer": f"fusion answer {n}",
        "wrong_answers": [f"f{n}a", f"f{n}b", f"f{n}c"],
        "difficulty": 3,
        "source_snippet": "News + Court",
        "is_fusion": True,
    }


class TestFusionReplayRegression:
    @pytest.mark.asyncio
    async def test_fusion_path_never_produces_fewer_questions_than_single_source_alone(self):
        from app.database import Profile, Question, SessionLocal
        from app.routes.profiles import _generate_questions_async

        db = SessionLocal()
        try:
            baseline_profile = Profile(name="Baseline Bert", content_quality="adequate")
            fusion_profile = Profile(name="Fusion Fred", content_quality="adequate")
            db.add_all([baseline_profile, fusion_profile])
            db.commit()
            db.refresh(baseline_profile)
            db.refresh(fusion_profile)
            baseline_id, fusion_id = baseline_profile.id, fusion_profile.id
        finally:
            db.close()

        raw_content = "\n".join(RAW_PARTS)
        base_questions = [_single_source_question(i) for i in range(5)]
        fusion_questions = [_fusion_question(i) for i in range(2)]

        # Single-source generator alone (fusion returns nothing — the pre-PHA-1510 shape).
        with patch("app.services.scraper.reddit.generate_questions", new=AsyncMock(return_value=base_questions)), \
             patch("app.services.fact_fusion.run_fact_fusion", new=AsyncMock(return_value=[])):
            await _generate_questions_async(
                baseline_id, raw_content, "Baseline Bert", budget=50, raw_parts=RAW_PARTS
            )

        # Same single-source output, but fusion now contributes extra questions.
        with patch("app.services.scraper.reddit.generate_questions", new=AsyncMock(return_value=base_questions)), \
             patch("app.services.fact_fusion.run_fact_fusion", new=AsyncMock(return_value=fusion_questions)):
            await _generate_questions_async(
                fusion_id, raw_content, "Fusion Fred", budget=50, raw_parts=RAW_PARTS
            )

        db = SessionLocal()
        try:
            baseline_count = db.query(Question).filter(Question.profile_id == baseline_id).count()
            fusion_count = db.query(Question).filter(Question.profile_id == fusion_id).count()
            fusion_marked = db.query(Question).filter(
                Question.profile_id == fusion_id, Question.is_fusion == True  # noqa: E712
            ).all()
            baseline_marked = db.query(Question).filter(
                Question.profile_id == baseline_id, Question.is_fusion == True  # noqa: E712
            ).all()
        finally:
            db.close()

        assert baseline_count == len(base_questions)
        assert fusion_count >= baseline_count, (
            "fusion path must only ADD questions on top of the single-source set, "
            f"got baseline={baseline_count} fusion={fusion_count}"
        )
        assert fusion_count == len(base_questions) + len(fusion_questions)
        assert len(fusion_marked) == len(fusion_questions)
        assert not baseline_marked, "no fusion questions were generated for the baseline profile"

    @pytest.mark.asyncio
    async def test_fusion_questions_deduped_against_single_source_by_text(self):
        """A fusion question that happens to restate a single-source one must not
        double-count — dedupe by normalized question_text."""
        from app.database import Profile, Question, SessionLocal
        from app.routes.profiles import _generate_questions_async

        db = SessionLocal()
        try:
            p = Profile(name="Dedup Dana", content_quality="adequate")
            db.add(p)
            db.commit()
            db.refresh(p)
            profile_id = p.id
        finally:
            db.close()

        base_questions = [_single_source_question(0), _single_source_question(1)]
        duplicate_fusion = dict(_single_source_question(0))  # identical question_text
        duplicate_fusion["is_fusion"] = True
        fusion_questions = [duplicate_fusion, _fusion_question(0)]

        with patch("app.services.scraper.reddit.generate_questions", new=AsyncMock(return_value=base_questions)), \
             patch("app.services.fact_fusion.run_fact_fusion", new=AsyncMock(return_value=fusion_questions)):
            await _generate_questions_async(
                profile_id, "\n".join(RAW_PARTS), "Dedup Dana", budget=50, raw_parts=RAW_PARTS
            )

        db = SessionLocal()
        try:
            count = db.query(Question).filter(Question.profile_id == profile_id).count()
        finally:
            db.close()

        # 2 base + 1 genuinely-new fusion question; the duplicate is dropped.
        assert count == 3
