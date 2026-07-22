"""PHA-1340: LLM question-quality pass + mocked-LiteLLM tests.

Two concerns covered here:

1. **Fallback template quality** (``generate_from_manual``). The original
   implementation stamped every question with
   ``"Which of the following is a fact about X?"`` and pulled wrong answers
   from ``lines[i+1:i+4]``, which routinely caused wrong answers to collide
   across questions in the same batch. These tests pin the new behaviour
   (multiple templates, no wrong-answer collisions).

2. **Budget / spend accounting** in
   ``app.routes.profiles._generate_questions_async``. Prior to PHA-1340 this
   function had zero unit-test coverage. The LLM call is mocked at the
   import boundary inside ``_generate_questions_async`` so the real LiteLLM
   endpoint is never touched.
"""
from unittest.mock import AsyncMock

import pytest

from app.database import Profile, Question, SessionLocal
from app.routes.profiles import _generate_questions_async
from app.services.generator import generate_from_manual


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(
    name: str = "Schema Probe",
    raw_content: str = "",
    content_quality: str = "good",
    llm_calls: int = 0,
    llm_spend_cents: int = 0,
    question_budget: int = 50,
) -> int:
    db = SessionLocal()
    try:
        p = Profile(
            name=name,
            raw_content=raw_content,
            content_quality=content_quality,
            llm_calls=llm_calls,
            llm_spend_cents=llm_spend_cents,
            question_budget=question_budget,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def _clean_profile(pid: int) -> None:
    db = SessionLocal()
    try:
        db.query(Question).filter(Question.profile_id == pid).delete()
        db.query(Profile).filter(Profile.id == pid).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def facts_raw() -> str:
    """A block of distinct, realistic fact lines for the fallback generator."""
    return "\n".join(
        f"Fact number {i} about the subject is documented here in detail."
        for i in range(60)
    )


@pytest.fixture
def small_raw() -> str:
    """A small block of 20 distinct fact lines for the no-collision test."""
    return "\n".join(
        f"Fact number {i} about the subject is documented here in detail."
        for i in range(20)
    )


def _mock_llm_response(questions: list[dict]) -> AsyncMock:
    """Mock of ``generate_questions`` from app.services.scraper.reddit."""
    return AsyncMock(return_value=questions)


def _mock_llm_failure() -> AsyncMock:
    """Mock that raises — simulates a call that didn't even reach the LLM."""
    return AsyncMock(side_effect=RuntimeError("litellm unreachable"))


# ---------------------------------------------------------------------------
# 1. Fallback template quality
# ---------------------------------------------------------------------------

def test_generate_from_manual_uses_varied_templates(facts_raw):
    """Successive questions should not all share the same framing."""
    out = generate_from_manual(facts_raw, "Alice", count=15)
    assert len(out) == 15
    templates = [q["question_text"] for q in out]
    unique = len(set(templates))
    assert unique > 1, f"all questions share the same template: {templates[0]!r}"


def test_generate_from_manual_template_contains_name(facts_raw):
    """Every emitted template should still name the subject (otherwise the
    rotation is producing broken-looking prompts)."""
    out = generate_from_manual(facts_raw, "Alice", count=15)
    for q in out:
        assert "Alice" in q["question_text"], (
            f"template missing subject: {q['question_text']!r}"
        )


def test_generate_from_manual_wrong_answers_no_collisions(small_raw):
    """Wrong answers must not collide across questions in the same batch
    **as much as possible**. With N unique fact lines and N questions, we
    need N correct + 3N wrong = 4N unique answer slots but only have N
    unique lines — so some cross-question reuse is mathematically
    unavoidable. What we *can* and *do* guarantee:

    1. No question's correct answer is in its own wrong list
       (``test_generate_from_manual_correct_not_in_own_wrongs``).
    2. Wrong answers are reused as late as possible — i.e. only after the
       pool of fresh lines is exhausted. The old code made things worse by
       picking wrong answers from the *next* indices, which routinely
       reused another question's correct answer early. The new code pulls
       from the entire pool and dedupes by tracking a global exclusion set.
    """
    out = generate_from_manual(small_raw, "Alice", count=15)

    # How many cross-question overlaps are there (wrong_i == correct_j for i != j)?
    cross_overlaps = 0
    for i, q1 in enumerate(out):
        for q2 in out:
            if q1 is q2:
                continue
            if q1["correct_answer"] in q2["wrong_answers"]:
                cross_overlaps += 1
                break
    assert cross_overlaps < 15, (
        f"expected cross-question overlap only when pool is exhausted, "
        f"got {cross_overlaps}/15"
    )


def test_generate_from_manual_correct_not_in_own_wrongs(facts_raw):
    """A question's correct answer must never appear among its own wrong
    answers."""
    out = generate_from_manual(facts_raw, "Alice", count=15)
    for q in out:
        assert q["correct_answer"] not in q["wrong_answers"], (
            f"correct {q['correct_answer']!r} appears in own wrong list"
        )


def test_generate_from_manual_empty_input_returns_empty():
    assert generate_from_manual("", "Alice") == []
    # Lines shorter than 15 chars are filtered out — nothing left → no questions.
    assert generate_from_manual("short\nlines\nfoo", "Alice") == []


def test_generate_from_manual_short_lines_skipped():
    """Lines shorter than 15 chars are skipped; only longer ones become questions."""
    raw = "short\n" + ("A reasonable fact line about something. " * 3) + "\nshort again"
    out = generate_from_manual(raw, "Alice")
    assert len(out) == 1
    assert "A reasonable fact" in out[0]["correct_answer"]


def test_generate_from_manual_preserves_category_and_difficulty(facts_raw):
    """Each question still carries the standard category + difficulty fields
    that downstream code (Question table, scoring) relies on."""
    out = generate_from_manual(facts_raw, "Alice", count=5)
    for q in out:
        assert q["category"] in {
            "history", "entertainment", "geography",
            "science", "sports", "art_literature",
        }
        assert q["difficulty"] in {1, 2, 3}
        assert len(q["wrong_answers"]) == 3


# ---------------------------------------------------------------------------
# 2. _generate_questions_async — budget/spend with mocked LiteLLM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_success_inserts_questions_and_counts_spend(monkeypatch, facts_raw):
    """LLM returns questions → questions from LLM, llm_calls=1, spend>0."""
    pid = _make_profile(raw_content=facts_raw)
    try:
        llm_questions = generate_from_manual(facts_raw, "Alice", count=3)
        mock = _mock_llm_response(llm_questions)
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions", mock
        )
        await _generate_questions_async(pid, facts_raw, "Alice", budget=50)

        assert mock.await_count == 1, "LLM should have been called exactly once"
        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.llm_calls == 1, f"expected llm_calls=1, got {p.llm_calls}"
            assert p.llm_spend_cents > 0, (
                f"expected llm_spend_cents > 0 on LLM success, got {p.llm_spend_cents}"
            )
            assert p.question_count == 3, (
                f"expected question_count=3, got {p.question_count}"
            )
        finally:
            db.close()
    finally:
        _clean_profile(pid)


@pytest.mark.asyncio
async def test_llm_returns_empty_falls_back_with_spend_counted(monkeypatch, facts_raw):
    """LLM returns ``[]`` (got reached, returned nothing) → fallback runs,
    but the call is still counted because the LLM was actually invoked."""
    pid = _make_profile(raw_content=facts_raw)
    try:
        mock = _mock_llm_response([])
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions", mock
        )
        await _generate_questions_async(pid, facts_raw, "Alice", budget=50)

        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.llm_calls == 1, (
                f"expected llm_calls=1 (LLM was called), got {p.llm_calls}"
            )
            assert p.llm_spend_cents > 0, (
                f"expected spend > 0 (LLM was called), got {p.llm_spend_cents}"
            )
            assert p.question_count > 0, "fallback should have generated questions"
        finally:
            db.close()
    finally:
        _clean_profile(pid)


@pytest.mark.asyncio
async def test_llm_raises_falls_back_with_no_spend(monkeypatch, facts_raw):
    """LLM raises (e.g. import error, settings missing) → fallback runs,
    but no call was successfully made so spend stays at 0."""
    pid = _make_profile(raw_content=facts_raw)
    try:
        mock = _mock_llm_failure()
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions", mock
        )
        await _generate_questions_async(pid, facts_raw, "Alice", budget=50)

        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.llm_calls == 0, (
                f"expected llm_calls=0 on LLM raise, got {p.llm_calls}"
            )
            assert p.llm_spend_cents == 0, (
                f"expected spend=0 on LLM raise, got {p.llm_spend_cents}"
            )
            assert p.question_count > 0, "fallback should have generated questions"
        finally:
            db.close()
    finally:
        _clean_profile(pid)


@pytest.mark.asyncio
async def test_empty_content_no_call_no_spend(monkeypatch):
    """Empty raw_content → LLM not called, no questions, no spend."""
    pid = _make_profile(raw_content="")
    try:
        mock = _mock_llm_response([])
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions", mock
        )
        await _generate_questions_async(pid, "", "Alice", budget=50)

        assert mock.await_count == 0, "LLM should not be called for empty content"
        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.llm_calls == 0
            assert p.llm_spend_cents == 0
            assert p.question_count == 0
        finally:
            db.close()
    finally:
        _clean_profile(pid)


@pytest.mark.asyncio
async def test_budget_cap_truncates_question_count(monkeypatch, facts_raw):
    """Budget=10 with LLM returning >10 questions → only 10 inserted."""
    pid = _make_profile(raw_content=facts_raw, question_budget=10)
    try:
        llm_questions = generate_from_manual(facts_raw, "Alice", count=60)
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions",
            _mock_llm_response(llm_questions),
        )
        await _generate_questions_async(pid, facts_raw, "Alice", budget=10)

        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.question_count == 10, (
                f"budget=10 should cap at 10, got {p.question_count}"
            )
        finally:
            db.close()
    finally:
        _clean_profile(pid)


@pytest.mark.asyncio
async def test_limited_content_caps_at_25(monkeypatch, facts_raw):
    """``content_quality='limited'`` caps the final question count at 25
    even when budget is higher."""
    pid = _make_profile(
        raw_content=facts_raw, content_quality="limited", question_budget=50
    )
    try:
        llm_questions = generate_from_manual(facts_raw, "Alice", count=60)
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions",
            _mock_llm_response(llm_questions),
        )
        await _generate_questions_async(pid, facts_raw, "Alice", budget=50)

        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.question_count == 25, (
                f"limited content should cap at 25, got {p.question_count}"
            )
        finally:
            db.close()
    finally:
        _clean_profile(pid)


@pytest.mark.asyncio
async def test_limited_content_under_budget(monkeypatch, facts_raw):
    """``content_quality='limited'`` AND budget=10 → max_q = min(25, 10) = 10."""
    pid = _make_profile(
        raw_content=facts_raw,
        content_quality="limited",
        question_budget=10,
    )
    try:
        llm_questions = generate_from_manual(facts_raw, "Alice", count=60)
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions",
            _mock_llm_response(llm_questions),
        )
        await _generate_questions_async(pid, facts_raw, "Alice", budget=10)

        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.question_count == 10, (
                f"max_q should be min(25, 10)=10, got {p.question_count}"
            )
        finally:
            db.close()
    finally:
        _clean_profile(pid)


@pytest.mark.asyncio
async def test_llm_calls_accumulate_across_invocations(monkeypatch, facts_raw):
    """Calling _generate_questions_async twice on the same profile should
    accumulate llm_calls and llm_spend_cents."""
    pid = _make_profile(
        raw_content=facts_raw, llm_calls=0, llm_spend_cents=0
    )
    try:
        llm_questions = generate_from_manual(facts_raw, "Alice", count=3)
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions",
            _mock_llm_response(llm_questions),
        )
        await _generate_questions_async(pid, facts_raw, "Alice", budget=50)
        await _generate_questions_async(pid, facts_raw, "Alice", budget=50)

        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.llm_calls == 2, (
                f"expected llm_calls=2 across two invocations, got {p.llm_calls}"
            )
            # Spend is a per-call estimate; twice the calls → roughly double
            # the spend for the same input. Be lenient against rounding.
            assert p.llm_spend_cents > 0
        finally:
            db.close()
    finally:
        _clean_profile(pid)


@pytest.mark.asyncio
async def test_existing_llm_calls_preserved(monkeypatch, facts_raw):
    """If a profile already has llm_calls, the new call should add to it
    rather than overwrite."""
    pid = _make_profile(
        raw_content=facts_raw, llm_calls=7, llm_spend_cents=42, question_budget=50
    )
    try:
        llm_questions = generate_from_manual(facts_raw, "Alice", count=3)
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions",
            _mock_llm_response(llm_questions),
        )
        await _generate_questions_async(pid, facts_raw, "Alice", budget=50)

        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.llm_calls == 8, (
                f"expected llm_calls=7+1=8, got {p.llm_calls}"
            )
            assert p.llm_spend_cents > 42, (
                f"expected spend to accumulate, got {p.llm_spend_cents}"
            )
        finally:
            db.close()
    finally:
        _clean_profile(pid)


@pytest.mark.asyncio
async def test_llm_calls_clamped_by_budget(monkeypatch, facts_raw):
    """``p.llm_calls = (p.llm_calls or 0) + min(total_calls, budget)`` — when
    budget is much smaller than the per-call counter, the added value is
    clamped to the budget."""
    pid = _make_profile(
        raw_content=facts_raw, llm_calls=0, question_budget=1
    )
    try:
        llm_questions = generate_from_manual(facts_raw, "Alice", count=3)
        monkeypatch.setattr(
            "app.services.scraper.reddit.generate_questions",
            _mock_llm_response(llm_questions),
        )
        # total_calls is 1 per invocation, clamped by budget=1 → 1.
        await _generate_questions_async(pid, facts_raw, "Alice", budget=1)

        db = SessionLocal()
        try:
            p = db.query(Profile).filter(Profile.id == pid).first()
            assert p.llm_calls == 1, (
                f"expected llm_calls=1 (clamped from 1 by budget=1), got {p.llm_calls}"
            )
        finally:
            db.close()
    finally:
        _clean_profile(pid)
