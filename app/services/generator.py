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

# ──────────────────────────────────────────────────────────────────────────────
# PHA-1562 fabricated-distractor strategies
#
# The corpus-pool fallback that PR #58 introduced gave us "real-but-different"
# facts from the same article (e.g., "The Loire" for a Paris question). Brandon
# Kelly's pivot was: that's still misleading. Every option read as a true fact.
#
# The fix: mutate the answer token itself so decoys are the same *kind* of thing
# (year → year, number → number, proper noun → proper noun) but never appear in
# the source material. A player who knows the subject can score a point with
# certainty; a player who doesn't cannot game the wrong answers.
# ──────────────────────────────────────────────────────────────────────────────

# Year decoys: decade-round-trip deltas. Never 0 (we want a real shift), always
# plausible (1000-2099 keeps the 4-digit shape), and the source's own decade is
# excluded by the source-tokens filter below.
_YEAR_DELTAS = (-50, -40, -30, -20, -10, 10, 20, 30, 40, 50)

# Number decoys: ±1% to ±10% shifts. Sign and magnitude picked from these
# buckets so we never produce the input value and always stay close enough to
# look like a plausible alternative (a 100,000 should not be the lie "12").
_NUMBER_PCT_BUCKETS = (1, 2, 3, 5, 7, 10)

# Curated proper-noun decoys. Real cities / places that are NEVER in the source
# by construction. They read as plausible answers ("Vienna", "Berlin") but
# answer nothing the player could verify from the quoted sentence. The list is
# long enough that any 3-subset is unlikely to be a real source fact.
_FABRICATED_PROPER_NOUNS = (
    "Vienna", "London", "Rome", "Berlin", "Cairo", "Madrid", "Athens",
    "Lisbon", "Prague", "Stockholm", "Dublin", "Helsinki", "Brussels",
    "Budapest", "Bucharest", "Oslo", "Copenhagen", "Geneva", "Zurich",
    "Lyon", "Marseille", "Naples", "Milan", "Munich", "Hamburg",
    "Edinburgh", "Manchester", "Liverpool", "Glasgow", "Cardiff", "Belfast",
    "Rotterdam", "Amsterdam", "Barcelona", "Seville", "Florence", "Venice",
    "Turin", "Porto", "Krakow", "Vilnius", "Riga", "Tallinn", "Reykjavik",
    "Sofia", "Belgrade", "Sarajevo", "Zagreb", "Ljubljana", "Skopje",
    "Tirana", "Thessaloniki", "Aleppo", "Yerevan", "Tbilisi", "Baku",
    "Tashkent", "Samarkand", "Bishkek", "Dushanbe", "Astana", "Minsk",
    "Kyiv", "Odesa", "Lviv", "Tallinn", "Riga",
)

# Edge-case placeholder decoys when no mutation strategy can produce 3 distinct,
# non-source, non-answer values. Real-shaped but obviously fabricated relative
# to any plausible trivia source — legendary places, famous-anchored years,
# distinctive numbers.
_FABRICATED_PLACEHOLDERS = {
    "year": ("1492", "1776", "1969"),
    "number": ("37", "2,468", "9,876"),
    "proper": ("Atlantis", "El Dorado", "Shangri-La"),
}


def _mutate_year(year: str) -> list[str]:
    """Return candidate year decoys by decade-round-trip deltas."""
    try:
        y = int(year)
    except ValueError:
        return []
    return [str(y + d) for d in _YEAR_DELTAS if 1000 <= y + d <= 2099 and y + d != y]


def _mutate_number(value: str) -> list[str]:
    """Return candidate number decoys by ±1-10% shift, preserving format.

    '100,000' → '99,000' / '105,000' / etc. '3.14' → '3.11' / '3.30' / etc.
    Keeps magnitude sensible: a 5-digit number never mutates to a 2-digit one.
    """
    raw = value.replace(",", "")
    try:
        n = float(raw)
    except ValueError:
        return []
    if n == 0:
        return []

    magnitude = max(1, len(raw.replace(".", "").lstrip("0") or "1"))
    candidates: list[str] = []
    for pct in _NUMBER_PCT_BUCKETS:
        for sign in (1, -1):
            shifted = n * (1 + sign * pct / 100)
            if shifted == n:
                continue
            s = _format_number(shifted)
            # Stay in the same order of magnitude as the input — a 100,000 must
            # not become 12. (For values < 1, the magnitude guard above keeps
            # them readable.)
            if len(s.replace(",", "").replace(".", "").lstrip("0") or "1") > magnitude + 1:
                continue
            candidates.append(s)
    return candidates


def _format_number(n: float) -> str:
    """Render a float the way it would have been typed: integers with thousands
    separators, decimals trimmed of trailing zeros."""
    if abs(n - round(n)) < 1e-9 and abs(n) < 1e15:
        return f"{int(round(n)):,}"
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def _decoys_for(kind: str, answer: str, source_tokens: set[str], n: int = 3) -> list[str]:
    """Return n fabricated decoys for an answer token of the given kind.

    Filters out the answer itself and anything in the source so decoys cannot
    accidentally collide with a fact from the corpus (which would re-introduce
    PHA-1562). Falls back to clearly-fabricated placeholders if mutation cannot
    produce enough distinct candidates.
    """
    if kind == "year":
        candidates = _mutate_year(answer)
    elif kind == "number":
        candidates = _mutate_number(answer)
    elif kind == "proper":
        candidates = list(_FABRICATED_PROPER_NOUNS)
    else:
        candidates = []

    out: list[str] = []
    for cand in candidates:
        if cand == answer or cand in source_tokens:
            continue
        if cand not in out:
            out.append(cand)
        if len(out) >= n:
            break

    if len(out) < n:
        for ph in _FABRICATED_PLACEHOLDERS.get(kind, ()):
            if ph == answer or ph in source_tokens or ph in out:
                continue
            out.append(ph)
            if len(out) >= n:
                break
    return out[:n]


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
    sentence and the player picks it from three *fabricated* same-typed decoys.
    Decoys are mutated from the answer (year shifts, number shifts, curated proper-
    noun swaps) and filtered against the entire source corpus so no decoy can
    accidentally be a real fact from the article. Someone who knows the subject can
    win; someone who doesn't cannot. Returns fewer questions — or none — rather
    than emitting a coin flip. (PHA-1562: "we need lies".)
    """
    raw_text = raw_text[: settings.content_max_chars]
    sentences = _sentences(raw_text)
    if not sentences:
        return []

    pools = _build_pools(sentences)
    # Every token in the corpus, regardless of kind, so a decoy that happens to
    # match a source fact in any category is filtered out before it reaches the
    # player. Without this, a year shift could land on a year from another
    # sentence and re-introduce the "real-but-different" PHA-1562 problem.
    source_tokens: set[str] = {tok for toks in pools.values() for tok in toks}
    rng = random.Random(f"{name}:{len(raw_text)}")  # deterministic per profile
    # The subject is named in the prompt, so blanking it out asks nothing.
    subject_words = {name} | set(name.split())

    # Round-robin the preferred kind so the decoy strategy varies across the
    # batch instead of always being year-shifts. cycle[0] is tried first, then
    # cycle[1], then cycle[2], then we fall back to anything that matches.
    priority_cycles = (
        ("year", "proper", "number"),
        ("proper", "number", "year"),
        ("number", "year", "proper"),
    )

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
        cycle = priority_cycles[len(questions) % len(priority_cycles)]
        tokens.sort(key=lambda t: cycle.index(t[0]) if t[0] in cycle else 99)

        for kind, answer, _initial in tokens:
            if answer in used_answers or answer in subject_words:
                continue
            decoys = _decoys_for(kind, answer, source_tokens, n=3)
            if len(decoys) < 3:
                # Mutation could not produce enough distinct, non-source candidates
                # for this answer. Try the next available token in the sentence
                # rather than padding with placeholder decoys.
                continue
            # Deterministic shuffle so the same input produces the same output.
            decoys = list(decoys)
            rng.shuffle(decoys)

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
