import random, json, re
from app.config import settings

CATEGORIES = ["history", "entertainment", "geography", "science", "sports", "art_literature"]

# The format this replaced. Any question shaped like "which of these is a fact about X?"
# with distractors drawn from the same fact pool is unanswerable — every option is true
# (PHA-1562). Kept as a guard so neither the LLM nor a future fallback can reintroduce it.
_UNANSWERABLE_PATTERNS = (
    re.compile(r"which of the following is a fact about", re.I),
    re.compile(r"which of these (?:is|are) (?:a )?(?:true|fact)", re.I),
)

_PLACEHOLDER_ANSWERS = {
    "never mentioned",
    "completely unrelated topic",
    "a different subject entirely",
}

# Token classes we can blank out of a sentence and still ask a fair question. Order
# matters: a year is the most recognisable thing to be quizzed on, a bare number the
# least, so we prefer the earlier ones when a sentence contains several.
_YEAR_RE = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2})\b")
_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_PROPER_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Words that open a sentence, so a leading capital is grammar rather than a name.
# Blanking one of these produces a question with no knowable answer.
_SENTENCE_STOPWORDS = {
    "The", "This", "That", "These", "Those", "There", "It", "Its", "They", "Their",
    "He", "His", "She", "Her", "In", "On", "At", "By", "For", "From", "With", "After",
    "Before", "During", "When", "While", "Although", "Because", "However", "Many",
    "Most", "Some", "Both", "Each", "As", "An", "And", "But", "Now", "Later", "Today",
}


def _sentences(raw_text: str) -> list[str]:
    """Split scraped text into candidate sentences, dropping fragments and headers."""
    out = []
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("=="):
            continue
        for sent in _SENTENCE_SPLIT_RE.split(line):
            sent = sent.strip()
            # Needs to stand alone once we quote it back at the player.
            if len(sent) < 40 or len(sent) > 300 or len(sent.split()) < 8:
                continue
            out.append(sent)
    return out


def _candidate_tokens(sentence: str) -> list[tuple[str, str, bool]]:
    """Return (kind, token, sentence_initial) triples that could be blanked out."""
    found: list[tuple[str, str, bool]] = []
    for m in _YEAR_RE.finditer(sentence):
        found.append(("year", m.group(0), m.start() == 0))
    for m in _NUMBER_RE.finditer(sentence):
        tok = m.group(0)
        if not _YEAR_RE.fullmatch(tok) and len(tok) > 1:
            found.append(("number", tok, m.start() == 0))
    for m in _PROPER_RE.finditer(sentence):
        phrase = m.group(0)
        if phrase.split()[0] in _SENTENCE_STOPWORDS:
            continue
        found.append(("proper", phrase, m.start() == 0))
    return found


def _build_pools(sentences: list[str]) -> dict[str, list[str]]:
    """Collect every blankable token in the corpus, bucketed by kind.

    Distractors come from these pools, so a wrong option is always the same *type* of
    thing as the answer — another year from the article, another name from the article —
    and is therefore plausible, while still being genuinely wrong for the blank being
    asked about. That is the property the old same-pool "pick the true fact" format
    never had.

    Sentence-initial capitals are excluded from the proper-noun pool: "Located",
    "Nicknamed" and "Administratively" all match the proper-noun shape at position 0.
    Requiring a word to appear capitalised *mid-sentence* somewhere in the corpus is
    what separates a real name from ordinary grammar.
    """
    pools: dict[str, list[str]] = {"year": [], "number": [], "proper": []}
    seen: dict[str, set[str]] = {k: set() for k in pools}
    for sent in sentences:
        for kind, tok, initial in _candidate_tokens(sent):
            if kind == "proper" and initial:
                continue
            if tok not in seen[kind]:
                seen[kind].add(tok)
                pools[kind].append(tok)
    return pools


def generate_from_manual(raw_text: str, name: str, count: int = 25) -> list[dict]:
    """Rule-based question fallback when the LLM is unavailable.

    Produces fill-in-the-blank questions: one distinctive token is removed from a real
    sentence and the player picks it from three same-typed decoys pulled from elsewhere
    in the same source. Someone who knows the subject can win; someone who doesn't
    cannot. Returns fewer questions — or none — rather than emitting a coin flip.
    """
    raw_text = raw_text[: settings.content_max_chars]
    sentences = _sentences(raw_text)
    if not sentences:
        return []

    pools = _build_pools(sentences)
    rng = random.Random(f"{name}:{len(raw_text)}")  # deterministic per profile
    priority = {"year": 0, "proper": 1, "number": 2}
    # The subject is named in the prompt, so blanking it out asks nothing.
    subject_words = {name} | set(name.split())

    questions: list[dict] = []
    used_answers: set[str] = set()

    for sent in sentences:
        if len(questions) >= count:
            break
        # Only ask about tokens the corpus confirms are real names/values, never a
        # capital that is just the start of a sentence.
        tokens = [t for t in _candidate_tokens(sent) if t[1] in pools[t[0]]]
        if not tokens:
            continue
        tokens.sort(key=lambda t: priority[t[0]])

        for kind, answer, _initial in tokens:
            if answer in used_answers or answer in subject_words:
                continue
            # A decoy that is visible in the quoted sentence reads as a typo, not a choice.
            pool = [t for t in pools[kind] if t != answer and t not in sent]
            if len(pool) < 3:
                continue
            decoys = rng.sample(pool, 3)

            blanked = sent.replace(answer, "______", 1)
            questions.append({
                "category": CATEGORIES[len(questions) % len(CATEGORIES)],
                "question_text": f'Fill in the blank about {name}: "{blanked}"',
                "correct_answer": answer[:200],
                "wrong_answers": [d[:200] for d in decoys],
                "difficulty": 1 if kind == "year" else 2,
                "source_snippet": sent[:100],
            })
            used_answers.add(answer)
            break

    return questions


def _iter_json_objects(raw: str):
    """Yield each complete top-level JSON object in `raw`, ignoring a truncated tail.

    A response cut off by max_tokens is an unterminated array, and json.loads discards
    all forty-odd questions that did arrive intact. Scanning balanced braces keeps them.
    """
    depth = 0
    start = None
    in_str = False
    escaped = False
    for i, ch in enumerate(raw):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    yield json.loads(raw[start:i + 1])
                except json.JSONDecodeError:
                    pass
                start = None


def validate_questions(items: list) -> list[dict]:
    """Drop anything unplayable before it reaches the database.

    A question survives only if it has three distinct wrong answers that all differ from
    the correct one. This is the gate that would have caught PHA-1562 at generation time.
    """
    out: list[dict] = []
    for q in items:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question_text", "")).strip()
        correct = str(q.get("correct_answer", "")).strip()
        if not text or not correct:
            continue
        if any(p.search(text) for p in _UNANSWERABLE_PATTERNS):
            continue

        raw_wrong = q.get("wrong_answers") or []
        if not isinstance(raw_wrong, list):
            continue
        wrong, seen = [], {correct.casefold()}
        for w in raw_wrong:
            w = str(w).strip()
            key = w.casefold()
            if not w or key in seen or key in _PLACEHOLDER_ANSWERS:
                continue
            seen.add(key)
            wrong.append(w)
        if len(wrong) < 3:
            continue

        cat = str(q.get("category", "")).strip()
        try:
            difficulty = int(q.get("difficulty", 1))
        except (TypeError, ValueError):
            difficulty = 1

        out.append({
            "category": cat if cat in CATEGORIES else "history",
            "question_text": text,
            "correct_answer": correct,
            "wrong_answers": wrong[:3],
            "difficulty": difficulty if difficulty in (1, 2, 3) else 1,
            "source_snippet": str(q.get("source_snippet", ""))[:100],
        })
    return out


def parse_llm_json_output(raw: str) -> list[dict]:
    """Parse LLM JSON output, stripping markdown code fences.

    Falls back to object-by-object salvage when the array is truncated, then validates.
    """
    if not raw:
        return []
    raw = re.sub(r"^```json\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return validate_questions(data)
        if isinstance(data, dict):
            return validate_questions([data])
        return []
    except json.JSONDecodeError:
        return validate_questions(list(_iter_json_objects(raw)))
