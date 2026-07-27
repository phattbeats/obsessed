"""PHA-1562 regression: every generated question must be answerable.

The shipped bug served questions of the form "Which of the following is a fact about
Paris?" with four true facts about Paris as the options. Scoring worked, the game loop
worked, and the game was still unwinnable by knowledge.

Two independent faults produced it, and both are pinned here:
  1. `settings.litellm_model` named a model the LiteLLM proxy does not serve, so the
     real generator 400'd on every call and silently fell back to rule-based questions.
  2. Even against a valid model, `max_tokens=4000` truncated a 50-question response
     mid-JSON, so the parse failed and landed on the same fallback.
"""
import json

import pytest

from app.config import settings
from app.services.generator import (
    generate_from_manual,
    parse_llm_json_output,
    validate_questions,
)

# A page of plain article text, of the shape the Wikipedia extract now returns.
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


def _options(q):
    return [q["correct_answer"], *q["wrong_answers"]]


class TestFallbackIsAnswerable:
    def test_produces_questions(self):
        assert generate_from_manual(SAMPLE_TEXT, "Marie Curie"), (
            "fallback produced nothing from clean article text"
        )

    def test_never_emits_the_unanswerable_format(self):
        for q in generate_from_manual(SAMPLE_TEXT, "Marie Curie"):
            assert "which of the following is a fact about" not in q["question_text"].lower()

    def test_correct_answer_is_never_also_a_wrong_answer(self):
        for q in generate_from_manual(SAMPLE_TEXT, "Marie Curie"):
            opts = _options(q)
            assert len(set(opts)) == len(opts), f"duplicate options in {q!r}"

    def test_always_offers_three_distractors(self):
        for q in generate_from_manual(SAMPLE_TEXT, "Marie Curie"):
            assert len(q["wrong_answers"]) == 3, q

    def test_no_placeholder_distractors(self):
        """The old fallback padded with 'Never mentioned' when it ran out of lines."""
        for q in generate_from_manual(SAMPLE_TEXT, "Marie Curie"):
            for w in q["wrong_answers"]:
                assert w.lower() != "never mentioned", q

    def test_distractors_are_absent_from_the_quoted_sentence(self):
        """A decoy visible in the quoted text is self-evidently wrong — free signal."""
        for q in generate_from_manual(SAMPLE_TEXT, "Marie Curie"):
            quoted = q["question_text"]
            for w in q["wrong_answers"]:
                assert w not in quoted, f"decoy {w!r} leaked into the prompt: {quoted!r}"

    def test_does_not_quiz_on_the_subject_name(self):
        """The subject is named in the prompt, so blanking it asks nothing."""
        for q in generate_from_manual(SAMPLE_TEXT, "Marie Curie"):
            assert q["correct_answer"] not in {"Marie Curie", "Marie", "Curie"}, q

    def test_returns_empty_rather_than_unanswerable(self):
        """Too little source text must yield no questions, not a coin flip."""
        assert generate_from_manual("Short. Too short.", "Nobody") == []


class TestValidationGate:
    def test_rejects_the_unanswerable_format(self):
        assert validate_questions([{
            "question_text": "Which of the following is a fact about Paris?",
            "correct_answer": "Paris was besieged by the Prussian Army.",
            "wrong_answers": ["Louis XVI was moved to the Tuileries.", "b", "c"],
        }]) == []

    def test_rejects_fewer_than_three_distinct_distractors(self):
        assert validate_questions([{
            "question_text": "In what year did X happen?",
            "correct_answer": "1903",
            "wrong_answers": ["1911", "1911", "1903"],
        }]) == []

    def test_rejects_a_distractor_equal_to_the_answer(self):
        assert validate_questions([{
            "question_text": "In what year did X happen?",
            "correct_answer": "1903",
            "wrong_answers": ["1903", "1911", "1914"],
        }]) == []

    def test_keeps_a_well_formed_question(self):
        out = validate_questions([{
            "category": "history",
            "question_text": "In what year did Marie Curie win the Nobel Prize in Physics?",
            "correct_answer": "1903",
            "wrong_answers": ["1911", "1914", "1934"],
            "difficulty": 2,
        }])
        assert len(out) == 1
        assert out[0]["wrong_answers"] == ["1911", "1914", "1934"]

    def test_unknown_category_falls_back_rather_than_dropping(self):
        out = validate_questions([{
            "category": "not_a_category",
            "question_text": "In what year?",
            "correct_answer": "1903",
            "wrong_answers": ["1911", "1914", "1934"],
        }])
        assert out and out[0]["category"] == "history"


class TestTruncatedResponseSalvage:
    """max_tokens truncation must cost the tail, not the whole batch."""

    def _batch(self, n):
        return [{
            "category": "history",
            "question_text": f"Question {i}?",
            "correct_answer": f"answer {i}",
            "wrong_answers": [f"w{i}a", f"w{i}b", f"w{i}c"],
            "difficulty": 1,
        } for i in range(n)]

    def test_salvages_complete_objects_from_a_cut_off_array(self):
        raw = json.dumps(self._batch(40))
        truncated = raw[: int(len(raw) * 0.7)]  # cut mid-object, no closing bracket
        salvaged = parse_llm_json_output(truncated)
        assert 20 <= len(salvaged) < 40, f"salvaged {len(salvaged)} of 40"

    def test_intact_array_parses_whole(self):
        assert len(parse_llm_json_output(json.dumps(self._batch(12)))) == 12

    def test_strips_markdown_fences(self):
        raw = "```json\n" + json.dumps(self._batch(3)) + "\n```"
        assert len(parse_llm_json_output(raw)) == 3

    @pytest.mark.parametrize("raw", ["", None, "not json at all", "[]"])
    def test_empty_and_garbage_inputs_return_empty(self, raw):
        """LiteLLM returns content=null on a truncated response; that must not raise."""
        assert parse_llm_json_output(raw) == []


class TestLiteLLMConfig:
    def test_model_is_namespaced(self):
        """A bare `claude-*` id 400s: 'no healthy deployments for this model'."""
        assert "/" in settings.litellm_model, (
            f"{settings.litellm_model!r} is not namespaced by provider — the proxy will "
            "reject it and every profile will silently serve fallback questions"
        )

    def test_output_ceiling_fits_a_full_batch(self):
        """50 full-sentence questions measured at ~8.8k completion tokens."""
        assert settings.litellm_max_tokens >= 12000, settings.litellm_max_tokens
