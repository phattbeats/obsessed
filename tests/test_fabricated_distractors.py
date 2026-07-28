"""PHA-1562 fabricated-distractor regression suite.

Brandon Kelly's pivot in review of PR #58: the real-but-different distractors
were still misleading ("What river runs through Paris?" with The Loire, The
Rhône, The Marne — all real rivers). Distractors must read as outright lies,
not alternatives a player could verify from the source.

This file pins three independent guarantees:

  1. The rule-based fallback (`generate_from_manual`) emits decoys that are
     *fabricated*: never pulled from the source material. The Paris reproducer
     in `TestParisReproducer` exercises this end-to-end; the per-strategy tests
     pin the helpers directly.

  2. The LLM prompt (built in `app/services/scraper/reddit.generate_questions`)
     contains an explicit fabrication rule, so a future prompt edit can't
     silently revert to "plausible but clearly wrong". Pinned in
     `TestLLMPromptRequiresFabrication`.

  3. When mutation cannot produce enough candidates (every plausible shift
     collides with a source token), the fallback emits the same-kind
     placeholder set so the question is still answerable. Pinned in
     `TestPlaceholderFallback`.
"""
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.generator import (
    _decoys_for,
    _mutate_number,
    _mutate_year,
    generate_from_manual,
)
from app.services.scraper.reddit import generate_questions


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

# Paris reproducer — the canonical PHA-1562 source text. Every fact in it must
# be absent from the generated decoys. Year/century of Paris facts are sparse,
# so the mutation-strategy tests below use the Marie Curie sample, which has
# real years and proper nouns to exercise.
PARIS_TEXT = (
    "Paris is the capital and most populous city of France, with an estimated "
    "population of 2,161,000 residents in the metropolitan area. "
    "The city is known worldwide for its iconic landmarks, including the Eiffel "
    "Tower, the Louvre, and Notre-Dame Cathedral. "
    "The Seine river runs through the center of Paris and divides the city into "
    "the Left Bank and the Right Bank. "
    "Paris was founded in the third century BC by a Celtic tribe called the "
    "Parisii, who settled on the Île de la Cité. "
    "Throughout its long history, the city has been a major center of European "
    "art, fashion, gastronomy, and culture, hosting countless world-class "
    "museums and institutions."
)

PARIS_FACTS = {
    # Proper nouns that show up in the text — a fabricated distractor must
    # never match any of these.
    "France", "Eiffel Tower", "Louvre", "Notre-Dame Cathedral", "Seine",
    "Left Bank", "Right Bank", "Parisii", "Celtic", "Île de la Cité",
    "Paris", "European",
}

# Marie Curie fixture — has real years (1867, 1903, 1911, 1914, 1934, 1935,
# 1944) and proper nouns (Warsaw, Paris, Sorbonne, Pierre Curie, Henri
# Becquerel, Radium Institute, etc.) to exercise both year-mutation and
# proper-noun-swap strategies.
SAMPLE_TEXT = """
Marie Curie was a physicist and chemist who conducted pioneering research on radioactivity.
She was born in Warsaw in 1867 and later moved to Paris to study at the Sorbonne.
Curie shared the Nobel Prize in Physics in 1903 with Pierre Curie and Henri Becquerel.
She received a second Nobel Prize, in Chemistry, in 1911 for the discovery of radium.
The Radium Institute was founded in 1914 and she directed it until her death.
Her daughter Irene Joliot-Curie also received a Nobel Prize, awarded in 1935.
Curie died in Passy in 1934 from aplastic anaemia caused by radiation exposure.
The element curium was named in her honour by Glenn Seaborg in 1944.
"""


def _all_source_tokens(text: str) -> set[str]:
    """Mirror of `generator._build_pools` — every blankable token in the text.

    Used by `TestParisReproducer` to assert that no decoy collides with any
    source fact, regardless of kind. We deliberately over-collect by also
    adding lowercase-stripped proper nouns (e.g. 'Paris' lowercase 'paris')
    so a fabricated distractor can't sneak in via case.
    """
    out: set[str] = set()
    for m in re.finditer(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", text):
        out.add(m.group(0))
    for m in re.finditer(r"\b\d[\d,]*(?:\.\d+)?\b", text):
        out.add(m.group(0))
    for m in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b", text):
        out.add(m.group(0))
    # Also lowercase forms so 'Paris' can't be paired with 'paris'.
    return out | {t.lower() for t in list(out)}


def _options(q):
    return [q["correct_answer"], *q["wrong_answers"]]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Paris reproducer — end-to-end fabricated decoys
# ─────────────────────────────────────────────────────────────────────────────

class TestParisReproducer:
    """The named PHA-1562 scenario: a Paris profile produces answerable questions.

    Every wrong answer must be a value that does NOT appear anywhere in the
    source text — neither as a year, nor a number, nor a proper noun. If a
    decoy shows up in the source, the question is back to "real-but-different"
    territory and the game is unwinnable by knowledge.
    """

    def test_paris_reproducer_produces_questions(self):
        qs = generate_from_manual(PARIS_TEXT, "Paris")
        assert qs, "fallback produced nothing from Paris source text"

    def test_no_decoy_collides_with_any_source_fact(self):
        """The strong invariant: every decoy must be absent from the source."""
        source_tokens = _all_source_tokens(PARIS_TEXT)
        qs = generate_from_manual(PARIS_TEXT, "Paris")
        assert qs, "fallback produced nothing"
        for q in qs:
            for w in q["wrong_answers"]:
                assert w not in source_tokens, (
                    f"decoy {w!r} collides with source fact in question "
                    f"{q['question_text']!r} — PHA-1562 regression"
                )

    def test_decoys_have_three_distinct_options(self):
        qs = generate_from_manual(PARIS_TEXT, "Paris")
        for q in qs:
            opts = _options(q)
            assert len(opts) == 4
            assert len(set(opts)) == 4, f"duplicate options in {q!r}"

    def test_decoys_match_kind_of_answer(self):
        """Same-kind grammar: a year needs year decoys, a proper noun needs
        proper-noun decoys. Otherwise the question reads as absurd."""
        year_re = re.compile(r"^\d{4}$")
        qs = generate_from_manual(PARIS_TEXT, "Paris")
        for q in qs:
            if year_re.match(q["correct_answer"]):
                for w in q["wrong_answers"]:
                    assert year_re.match(w), (
                        f"year answer {q['correct_answer']!r} got non-year "
                        f"decoy {w!r}"
                    )
            elif q["correct_answer"][:1].isupper():
                for w in q["wrong_answers"]:
                    assert w[:1].isupper() or not w[0].isdigit(), (
                        f"proper-noun answer {q['correct_answer']!r} got "
                        f"non-proper decoy {w!r}"
                    )

    def test_decoys_are_absent_from_the_quoted_sentence(self):
        """A decoy visible in the quoted text is self-evidently wrong — free signal."""
        qs = generate_from_manual(PARIS_TEXT, "Paris")
        for q in qs:
            quoted = q["question_text"]
            for w in q["wrong_answers"]:
                assert w not in quoted, (
                    f"decoy {w!r} leaked into the prompt: {quoted!r}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Mutation strategy unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestYearMutation:
    def test_year_shifts_stay_in_plausible_range(self):
        candidates = _mutate_year("1867")
        assert candidates, "no year candidates generated"
        for c in candidates:
            assert 1000 <= int(c) <= 2099, f"{c} outside plausible 4-digit range"
            assert c != "1867", "year shift produced the source year"

    def test_year_shifts_use_decade_round_trip(self):
        """Allowed deltas: ±10/20/30/40/50. ±1 would be too subtle to read as a lie."""
        for c in _mutate_year("1900"):
            delta = abs(int(c) - 1900)
            assert delta in {10, 20, 30, 40, 50}, (
                f"unexpected year delta {delta} for candidate {c!r}"
            )

    def test_year_shifts_never_equal_source(self):
        for c in _mutate_year("2000"):
            assert c != "2000"

    def test_year_shifts_handles_centuries(self):
        # 999 → 1009 is the closest plausible; 2099 + 10 = 2109 must be rejected.
        assert "1009" in _mutate_year("999")
        assert "2109" not in _mutate_year("2099")

    def test_year_shifts_handles_garbage(self):
        assert _mutate_year("not a year") == []
        assert _mutate_year("") == []


class TestNumberMutation:
    def test_number_shifts_preserve_order_of_magnitude(self):
        candidates = _mutate_number("100,000")
        for c in candidates:
            # 100,000 shifted ±10% is 90,000-110,000. None should be tiny.
            assert int(c.replace(",", "")) >= 80_000
            assert int(c.replace(",", "")) <= 120_000

    def test_number_shifts_preserve_thousands_separator(self):
        candidates = _mutate_number("100,000")
        for c in candidates:
            assert "," in c, f"thousands separator dropped from {c!r}"

    def test_number_shifts_preserve_decimal_places(self):
        candidates = _mutate_number("3.14")
        for c in candidates:
            assert "." in c, f"decimal point dropped from {c!r}"

    def test_number_shifts_never_equal_source(self):
        for c in _mutate_number("42"):
            assert c != "42"

    def test_number_shifts_handle_zero_without_crashing(self):
        # 0 would produce ±0% = 0 forever; the helper must skip it.
        assert _mutate_number("0") == []

    def test_number_shifts_handle_garbage(self):
        assert _mutate_number("not a number") == []
        assert _mutate_number("") == []


class TestProperNounSwap:
    def test_proper_noun_decoys_come_from_curated_list(self):
        from app.services.generator import _FABRICATED_PROPER_NOUNS
        decoys = _decoys_for("proper", "Paris", set())
        assert len(decoys) == 3
        for d in decoys:
            assert d in _FABRICATED_PROPER_NOUNS, (
                f"proper-noun decoy {d!r} not in curated list"
            )

    def test_proper_noun_decoys_exclude_the_answer(self):
        # If "Paris" itself were in the curated list, it must be skipped.
        decoys = _decoys_for("proper", "Paris", set())
        assert "Paris" not in decoys

    def test_proper_noun_decoys_exclude_source_tokens(self):
        # If a curated city happens to appear in the source corpus, it must
        # be filtered out (this is how PHA-1562 would silently regress).
        decoys = _decoys_for("proper", "Paris", {"Vienna", "London"})
        assert "Vienna" not in decoys
        assert "London" not in decoys

    def test_proper_noun_decoys_are_three_distinct_values(self):
        decoys = _decoys_for("proper", "Paris", set())
        assert len(set(decoys)) == 3


# ─────────────────────────────────────────────────────────────────────────────
# 3. End-to-end generator + validate_questions integration
# ─────────────────────────────────────────────────────────────────────────────

class TestGeneratorEmitsFabricatedDecoys:
    """generate_from_manual must produce fabricated decoys for the Marie Curie
    sample. This is the wider net: it covers any future change to the
    priority/round-robin logic."""

    def test_marie_curie_decoys_are_not_in_source(self):
        source_tokens = _all_source_tokens(SAMPLE_TEXT)
        qs = generate_from_manual(SAMPLE_TEXT, "Marie Curie")
        assert qs, "fallback produced nothing"
        for q in qs:
            for w in q["wrong_answers"]:
                assert w not in source_tokens, (
                    f"decoy {w!r} collides with source fact in {q!r}"
                )

    def test_year_decoys_are_not_the_source_year(self):
        qs = generate_from_manual(SAMPLE_TEXT, "Marie Curie")
        year_qs = [q for q in qs if re.match(r"^\d{4}$", q["correct_answer"])]
        assert year_qs, "expected at least one year question"
        for q in year_qs:
            for w in q["wrong_answers"]:
                assert w != q["correct_answer"]

    def test_round_robin_emits_varied_kinds(self):
        """The brief requires round-robin so decoy strategies vary across the
        batch. With a sample that has years AND proper nouns, the generator
        should emit at least one of each kind across a 7-question batch."""
        qs = generate_from_manual(SAMPLE_TEXT, "Marie Curie")
        kinds = set()
        for q in qs:
            if re.match(r"^\d{4}$", q["correct_answer"]):
                kinds.add("year")
            elif q["correct_answer"][:1].isupper() and not q["correct_answer"][0].isdigit():
                kinds.add("proper")
            elif any(ch.isdigit() for ch in q["correct_answer"]):
                kinds.add("number")
        # Sample text has years and proper nouns but no plain numbers; both
        # should appear in the output.
        assert {"year", "proper"} <= kinds, f"only emitted kinds: {kinds}"

    def test_decoys_never_contain_never_mentioned_placeholder(self):
        """The old fallback padded with 'never mentioned' when it ran out."""
        qs = generate_from_manual(SAMPLE_TEXT, "Marie Curie")
        for q in qs:
            for w in q["wrong_answers"]:
                assert w.lower() != "never mentioned"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Placeholder fallback when mutation cannot produce enough candidates
# ─────────────────────────────────────────────────────────────────────────────

class TestPlaceholderFallback:
    """When the source-tokens filter blocks every mutation, the fallback must
    emit same-kind placeholder decoys so the question is still answerable."""

    def test_year_kind_falls_back_to_placeholder_when_all_shifts_blocked(self):
        # Block every plausible shift for "1900".
        blocked = set(_mutate_year("1900")) | {"1900"}
        decoys = _decoys_for("year", "1900", blocked, n=3)
        assert len(decoys) == 3
        for d in decoys:
            assert re.match(r"^\d{4}$", d), f"non-year placeholder: {d!r}"
            assert d != "1900"

    def test_number_kind_falls_back_to_placeholder_when_all_shifts_blocked(self):
        # Block every plausible shift for "100".
        blocked = set(_mutate_number("100")) | {"100"}
        # _mutate_number may return [] for some inputs; that's fine — the
        # assertion below is what matters.
        if not blocked - {"100"}:
            pytest.skip("no candidate shifts for 100, can't exercise fallback")
        decoys = _decoys_for("number", "100", blocked, n=3)
        assert len(decoys) == 3
        for d in decoys:
            assert d != "100"
            # Same-kind: numeric.
            assert re.sub(r"[,\.]", "", d).isdigit(), f"non-number placeholder: {d!r}"

    def test_proper_kind_falls_back_to_placeholder_when_curated_list_blocked(self):
        from app.services.generator import _FABRICATED_PROPER_NOUNS
        # Block every curated proper noun.
        blocked = set(_FABRICATED_PROPER_NOUNS) | {"Paris"}
        decoys = _decoys_for("proper", "Paris", blocked, n=3)
        assert len(decoys) == 3
        for d in decoys:
            assert d not in blocked
            assert d != "Paris"
            # Same-kind: starts with a capital.
            assert d[:1].isupper(), f"non-proper placeholder: {d!r}"

    def test_placeholder_decoys_are_distinct_from_each_other(self):
        blocked = set(_mutate_year("1900")) | {"1900"}
        decoys = _decoys_for("year", "1900", blocked, n=3)
        assert len(set(decoys)) == 3


# ─────────────────────────────────────────────────────────────────────────────
# 5. LLM prompt contract — must require fabrication
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_client(captured: dict, response_text: str = "[]") -> MagicMock:
    """Mirror of tests/test_music_question_generator.py — captures the request
    body so we can assert on the system prompt without a live API call."""
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


class TestLLMPromptRequiresFabrication:
    """The LLM prompt must explicitly require fabricated distractors. If a
    future prompt edit silently reverts to 'plausible but clearly wrong',
    these tests catch it before it ships."""

    @pytest.mark.asyncio
    async def test_prompt_requires_fabricated_distractors(self):
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            await generate_questions(
                profile_id=1, raw_content=PARIS_TEXT, name="Paris"
            )
        system_prompt = captured["json"]["messages"][0]["content"]
        assert "fabricated" in system_prompt.lower(), (
            "LLM prompt must require fabricated distractors "
            "(PHA-1562 pivot: 'we need lies'). "
            f"Got: {system_prompt[:200]!r}..."
        )

    @pytest.mark.asyncio
    async def test_prompt_omits_old_plausible_but_clearly_wrong_rule(self):
        """The old rule produced real-but-different distractors. The new rule
        must replace it; this test catches a copy-paste that kept the old
        sentence in addition to the new one."""
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            await generate_questions(
                profile_id=1, raw_content=PARIS_TEXT, name="Paris"
            )
        system_prompt = captured["json"]["messages"][0]["content"].lower()
        assert "plausible but clearly wrong" not in system_prompt, (
            "old 'plausible but clearly wrong' rule must be replaced "
            "by the fabrication rule, not left in alongside it"
        )

    @pytest.mark.asyncio
    async def test_prompt_includes_worked_examples(self):
        """The prompt must show the model (GOOD + BAD) examples so it can
        pattern-match what fabrication looks like vs. the PHA-1562 bug."""
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            await generate_questions(
                profile_id=1, raw_content=PARIS_TEXT, name="Paris"
            )
        system_prompt = captured["json"]["messages"][0]["content"]
        assert "GOOD" in system_prompt and "BAD" in system_prompt, (
            "prompt must include GOOD/BAD worked examples"
        )

    @pytest.mark.asyncio
    async def test_prompt_targets_trivia_shaped_facts(self):
        """The question_text rule should steer the model away from yes/no
        questions and toward year/name/number/place/work facts."""
        captured: dict = {}
        with patch(
            "app.services.scraper.reddit.httpx.AsyncClient",
            return_value=_make_mock_client(captured),
        ):
            await generate_questions(
                profile_id=1, raw_content=PARIS_TEXT, name="Paris"
            )
        system_prompt = captured["json"]["messages"][0]["content"].lower()
        # Confirm the trivia-shape rule is present; the actual enum
        # (year/name/number/place/work) is a free-text guideline.
        assert "trivia" in system_prompt or "year" in system_prompt, (
            "prompt must require trivia-shaped questions"
        )