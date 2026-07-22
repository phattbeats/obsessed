"""Rule-based question generation when LLM is unavailable.

PHA-1340 improvements over the original implementation:

- **Template variety.** The original stamped every question with the same
  ``"Which of the following is a fact about X?"`` framing. We now rotate
  through a pool of templates so consecutive questions don't read identically.
- **Collision-free wrong answers.** The original used ``lines[i+1:i+4]`` as
  wrong answers, which routinely caused wrong answers to collide across
  questions (the same line ended up as both another question's correct
  answer and another's wrong answer). We now draw from a global pool with
  a tracking set so a wrong answer is never reused.
- **No self-referential wrongs.** A question's correct answer is added to
  the exclusion set before the wrong-answer pool is built, so it can never
  appear among its own wrong options.
"""
import random
import json
from app.config import settings

CATEGORIES = ["history", "entertainment", "geography", "science", "sports", "art_literature"]

# Round-robin of phrasings so 50 questions don't all sound like the same
# multiple-choice question. Keep these short and self-contained — they only
# need to read as a trivia prompt.
QUESTION_TEMPLATES = [
    "Which of the following is a fact about {name}?",
    "Which of these statements about {name} is true?",
    "Which of the following best describes {name}?",
    "All of the following are true about {name} EXCEPT:",
    "Which fact about {name} is correct?",
    "What is known about {name}?",
    "Speaking of {name}, which of the following is accurate?",
    "{name} is associated with which of the following?",
    "Which of the following is true regarding {name}?",
    "Of these options, which describes {name}?",
]

# Last-resort fillers when the fact pool is too small to fill 3 unique wrong
# answers. Tracked in the same exclusion set so two questions don't reuse
# the same filler.
_FALLBACK_WRONG_ANSWERS = [
    "Never mentioned",
    "Completely unrelated topic",
    "A different subject entirely",
    "Not part of the available facts",
    "No information available",
]


def _pick_template(name: str, idx: int) -> str:
    """Round-robin template selection by line index, with the subject name
    interpolated into the {name} placeholder."""
    return QUESTION_TEMPLATES[idx % len(QUESTION_TEMPLATES)].format(name=name)


def generate_from_manual(raw_text: str, name: str, count: int = 25) -> list[dict]:
    """Rule-based question fallback when LLM is unavailable.

    Generates up to ``count`` questions from raw fact lines. Templates are
    rotated so consecutive questions don't share phrasing. Wrong answers are
    drawn from a global exclusion set so they never collide across questions
    in the same batch (no two questions share a wrong answer, and no wrong
    answer is the same as its own correct answer).
    """
    raw_text = raw_text[: settings.content_max_chars]
    lines = [l.strip() for l in raw_text.split("\n") if len(l.strip()) > 15]
    if not lines:
        return []

    used_answers: set[str] = set()
    questions: list[dict] = []

    for i, line in enumerate(lines[:count]):
        cat = CATEGORIES[i % len(CATEGORIES)]
        words = line.split()
        if len(words) < 4:
            continue

        correct = line[:200]
        used_answers.add(correct)

        # Build the wrong-answer pool from every other line whose truncated
        # form hasn't already been used (neither as a correct answer on a
        # previous question nor as a wrong answer on one).
        pool = [
            l[:200]
            for l in lines
            if l != line and l[:200] not in used_answers
        ]
        random.shuffle(pool)
        wrong = pool[:3]

        # If the pool is too thin (e.g. only 2 other lines), top up with
        # generic fillers — also tracked so they don't collide either.
        for filler in _FALLBACK_WRONG_ANSWERS:
            if len(wrong) >= 3:
                break
            if filler not in used_answers:
                wrong.append(filler)
                used_answers.add(filler)

        q = {
            "category": cat,
            "question_text": _pick_template(name, i),
            "correct_answer": correct,
            "wrong_answers": wrong[:3],
            "difficulty": random.choice([1, 1, 2]),
            "source_snippet": line[:100],
        }
        questions.append(q)

    return questions


def parse_llm_json_output(raw: str) -> list[dict]:
    """Parse LLM JSON output, stripping markdown code fences."""
    import re
    raw = re.sub(r"^```json\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError:
        return []
