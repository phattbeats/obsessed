"""
PHA-1510 — fact-fusion stage for the trivia question generator.

Goal: instead of only generating single-source trivia questions (the
existing `app.services.scraper.reddit.generate_questions` path), merge
facts scraped from *multiple* sources about the same person into harder
"multi-hop" questions — e.g. combine a news-article fact with a
court-record fact into one question that requires knowing both.

Cost shape (Brandon's product decision — see PHA-1510): exactly TWO new
LLM calls per scrape, batched, not one-call-per-source or
one-call-per-fact-pair:
  1. `extract_facts()`  — one call, given ALL source blocks together
     (each tagged with its source label/index), returns atomic facts
     tagged by source.
  2. `generate_fusion_questions()` — one call, given ALL matched
     cross-source fact pairs together, returns multiple multi-hop
     questions in one shot.
`match_pairs()` is pure Python (no LLM) — it groups the extracted facts
by shared entity/keyword and keeps only pairs backed by >=2 distinct
sources. This keeps the added cost at +2 calls per scrape on top of the
existing ~1 call baseline from `reddit.generate_questions`, not +3/+4.

Rollout (product decision): no feature flag, no A/B gate — this is wired
directly into the normal scrape flow in `app/routes/profiles.py`.

Bridging (product decision): fact pairs are matched across ANY two
different sources that share a common entity/keyword (person name,
place, organization, date-anchored event, etc.), extracted generically
by the LLM in `extract_facts()` — NOT restricted to a hardcoded
allowlist of source-type pairs. `_INTERESTING_PAIRS` below is only a
ranking bias (a tiebreaker/boost toward historically strong combos like
news+court or music+wikipedia+obituary for genealogy); it never filters
out a pair. Any pair with source_count >= 2 is eligible.

IMPORTANT CAVEAT: the original PHA-1510 design doc is not available in
this environment (it lived in a different agent's sandboxed workspace
and was never committed to this repo or pushed to GitHub). Everything
below — the fact schema, the entity-matching heuristic, the ranking
formula, the Question-table marker vs. a dedicated fusion table, and the
prompt wording — is this implementation's own judgment call made from
the codebase conventions in `app/services/scraper/reddit.py` and
`app/services/generator.py`, plus the three product decisions above. It
is not a literal transcription of a prior spec.
"""
import json
import os
import re

import httpx

from app.config import settings

CATEGORIES = ["history", "entertainment", "geography", "science", "sports", "art_literature"]

# Mirrors reddit.LAST_LLM_ERROR — a silent fallback is how PHA-1562 shipped, so both
# fusion LLM calls record why they produced nothing.
LAST_FUSION_ERROR: dict[str, str] = {"reason": ""}

# Minimum distinct sources a fact-group needs before it's worth asking the LLM to
# fuse. Below this there is nothing to bridge.
_MIN_SOURCES_FOR_FUSION = 2

# Cap how many pairs we send to generate_fusion_questions in one batched call, so a
# profile with a huge cross-source overlap doesn't blow the token budget.
_MAX_PAIRS_PER_CALL = 30

# Soft ranking bias only — NOT a hard filter (product decision #3). Keys are
# frozensets of source *types* (see `_infer_source_type`); a pair whose two source
# types match a key gets its score boosted by the value. Any other combination of
# >=2 distinct sources is still eligible, just unboosted.
_INTERESTING_PAIRS: dict[frozenset, int] = {
    frozenset({"news", "court"}): 3,
    frozenset({"news", "sos"}): 2,
    frozenset({"news", "auditor"}): 2,
    frozenset({"news", "voter"}): 2,
    frozenset({"court", "sos"}): 2,
    frozenset({"auditor", "sos"}): 2,
    frozenset({"auditor", "voter"}): 1,
    frozenset({"music", "books"}): 2,
    frozenset({"music", "wikipedia"}): 2,
    frozenset({"books", "wikipedia"}): 2,
    frozenset({"music", "obituary"}): 3,
    frozenset({"wikipedia", "obituary"}): 3,
    frozenset({"books", "obituary"}): 2,
    frozenset({"auditor", "auditor"}): 2,  # e.g. two separate property records
}

# Known bracket-label prefixes -> a coarse source "type" bucket, used only to look
# up `_INTERESTING_PAIRS` weights. Matching is substring-based and best-effort; an
# unrecognized label still participates in fusion, it just gets no ranking boost.
_SOURCE_TYPE_MARKERS: list[tuple[str, str]] = [
    ("news results for", "news"),
    ("court docket for", "court"),
    ("sos business entities for", "sos"),
    ("property records for", "auditor"),
    ("ohio voter registration for", "voter"),
    ("last.fm profile", "music"),
    ("spotify profile", "music"),
    ("openlibrary", "books"),
    ("wikipedia", "wikipedia"),
    ("wikidata", "wikipedia"),
    ("obituary", "obituary"),
    ("find a grave", "obituary"),
    ("reddit", "reddit"),
    ("instagram", "instagram"),
    ("facebook", "facebook"),
    ("tiktok", "tiktok"),
    ("pinterest", "pinterest"),
    ("steam", "steam"),
    ("twitter", "twitter"),
    ("places", "places"),
    ("openstreetmap", "places"),
    ("travel", "travel"),
    ("events", "events"),
    ("things", "things"),
]

_LABEL_RE = re.compile(r"^\s*\[([^\]]+)\]")


def _source_label(block: str, index: int) -> str:
    """Pull the human-readable label out of a raw_parts block's leading bracket tag.

    Every source block emitted by trigger_scrape's helpers starts with a bracket
    label like "[News results for: ...]" or "[Reddit u_spez]". Falls back to a
    generic "Source N" label for blocks that don't follow the convention (e.g. a
    manual-facts block has no bracket tag at all).
    """
    m = _LABEL_RE.match(block or "")
    if m:
        return m.group(1).strip()
    return f"Source {index}"


def _infer_source_type(label: str) -> str:
    """Map a source label to a coarse type bucket for the interesting-pairs weight map.

    This is deliberately loose (substring match) and non-exhaustive — it only
    affects ranking bias, never eligibility. An unmatched label just becomes its
    own type bucket (its lowercased first word), so two blocks with the same
    unrecognized label still count as "the same type" for the "auditor+auditor"
    style combos without needing an explicit entry for every possible label.
    """
    low = label.lower()
    for marker, kind in _SOURCE_TYPE_MARKERS:
        if marker in low:
            return kind
    return low.split(":")[0].split()[0] if low.split() else "unknown"


def _pair_weight(type_a: str, type_b: str) -> int:
    key = frozenset({type_a, type_b})
    return _INTERESTING_PAIRS.get(key, 0)


def _extract_json_array(content: str) -> list[dict]:
    """Best-effort JSON-array parser for LLM output, tolerant of markdown fences
    and a bit of surrounding chatter. Mirrors the tolerance of
    `app.services.generator.parse_llm_json_output` but returns raw dicts —
    fact/pair shapes aren't Question rows, so `validate_questions()` doesn't apply.
    """
    if not content:
        return []
    text = re.sub(r"^```(?:json)?\s*", "", content.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _api_key() -> str:
    return os.environ.get("LITELLM_API_KEY", "") or settings.litellm_api_key or ""


async def extract_facts(raw_parts: list[str]) -> list[dict]:
    """One batched LLM call: pull atomic, standalone facts out of ALL source
    blocks together, each tagged with the source it came from.

    Returns a list of dicts shaped:
        {"source_index": int, "source_label": str, "fact_text": str, "entities": [str, ...]}
    `entities` are generic shared keywords (person names, places, organizations,
    date-anchored events) the LLM thinks another source might also mention — this
    is what lets `match_pairs()` bridge sources without a hardcoded allowlist.

    Returns [] (never raises) when there's nothing to extract from, or the LLM
    call fails/returns unusable output.
    """
    blocks = [b for b in (raw_parts or []) if b and b.strip()]
    if len(blocks) < _MIN_SOURCES_FOR_FUSION:
        return []

    numbered = []
    for i, block in enumerate(blocks):
        label = _source_label(block, i)
        # Cap each block so one giant source (e.g. a Wikipedia extract) doesn't
        # crowd out the others in the shared prompt budget.
        snippet = block[:4000]
        numbered.append(f"### Source {i}: {label}\n{snippet}")
    user_prompt = "\n\n".join(numbered)

    system_prompt = """You extract atomic, standalone facts from multiple tagged source blocks about the same person, for a trivia-question pipeline.

For EACH source block, pull out 3-8 short, self-contained facts (skip a block if it has nothing factual). A fact must stand alone without needing the rest of the block for context.

For each fact, also list "entities": generic shared keywords that another, different source about the same person might ALSO mention — full person names, place names, organization names, or date-anchored events (e.g. "2019", "Franklin County Municipal Court", "Delaware, Ohio", "Acme LLC"). These are used to bridge facts across sources, so favor specific, reusable strings over generic ones.

Return ONLY a JSON array (no markdown, no commentary), each element:
{"source_index": 0, "source_label": "...", "fact_text": "...", "entities": ["...", "..."]}

source_index must be the integer from the "### Source N: ..." heading the fact came from. Do not invent facts not supported by the text."""

    LAST_FUSION_ERROR["reason"] = ""
    try:
        api_key = _api_key()
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{settings.litellm_base}/chat/completions",
                json={
                    "model": settings.litellm_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": settings.litellm_max_tokens,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            content = choice.get("message", {}).get("content") or ""
            facts = _extract_json_array(content)
            if not facts:
                LAST_FUSION_ERROR["reason"] = (
                    f"extract_facts: no usable facts parsed (finish_reason={choice.get('finish_reason')}, "
                    f"{len(content)} chars returned)"
                )
                return []
    except Exception as e:
        LAST_FUSION_ERROR["reason"] = f"extract_facts: {type(e).__name__}: {e}"
        return []

    out = []
    for f in facts:
        try:
            idx = int(f.get("source_index", -1))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(blocks):
            continue
        fact_text = str(f.get("fact_text", "")).strip()
        if not fact_text:
            continue
        entities = f.get("entities") or []
        if not isinstance(entities, list):
            entities = []
        entities = [str(e).strip() for e in entities if str(e).strip()]
        label = str(f.get("source_label") or _source_label(blocks[idx], idx)).strip()
        out.append({
            "source_index": idx,
            "source_label": label,
            "source_type": _infer_source_type(label),
            "fact_text": fact_text[:500],
            "entities": entities,
        })
    return out


def match_pairs(facts: list[dict]) -> list[dict]:
    """Pure Python, no LLM. Group facts by shared entity/keyword and keep only
    pairs backed by >= 2 DISTINCT sources, ranked by source_count then the
    `_INTERESTING_PAIRS` weight map (a bias, not a filter — see module docstring).

    Returns a list of dicts:
        {"entity": str, "fact_a": dict, "fact_b": dict, "source_count": 2,
         "weight": int, "score": float}
    sorted best-first.
    """
    if not facts:
        return []

    # Case-insensitive bucket by entity string -> facts that mention it.
    by_entity: dict[str, list[dict]] = {}
    for fact in facts:
        for ent in fact.get("entities", []):
            key = ent.strip().lower()
            if len(key) < 2:
                continue
            by_entity.setdefault(key, []).append(fact)

    seen_pairs: set[tuple] = set()
    pairs: list[dict] = []
    for entity_key, ent_facts in by_entity.items():
        # Only distinct sources count as a bridge — two facts from the same
        # source block sharing an entity isn't cross-source fusion.
        by_source: dict[int, list[dict]] = {}
        for f in ent_facts:
            by_source.setdefault(f["source_index"], []).append(f)
        distinct_sources = sorted(by_source.keys())
        if len(distinct_sources) < _MIN_SOURCES_FOR_FUSION:
            continue

        # Pairwise across distinct sources (keep it 2-hop — matches the "combine
        # a news fact with a court fact" shape from the issue).
        for i in range(len(distinct_sources)):
            for j in range(i + 1, len(distinct_sources)):
                src_a, src_b = distinct_sources[i], distinct_sources[j]
                fact_a = by_source[src_a][0]
                fact_b = by_source[src_b][0]
                dedupe_key = (min(src_a, src_b), max(src_a, src_b),
                              fact_a["fact_text"][:80], fact_b["fact_text"][:80])
                if dedupe_key in seen_pairs:
                    continue
                seen_pairs.add(dedupe_key)

                weight = _pair_weight(fact_a["source_type"], fact_b["source_type"])
                source_count = 2
                score = source_count * 10 + weight
                pairs.append({
                    "entity": entity_key,
                    "fact_a": fact_a,
                    "fact_b": fact_b,
                    "source_count": source_count,
                    "weight": weight,
                    "score": score,
                })

    pairs.sort(key=lambda p: p["score"], reverse=True)
    return pairs[:_MAX_PAIRS_PER_CALL]


async def generate_fusion_questions(
    profile_id: int,
    name: str,
    pairs: list[dict],
    category_hint: str | None = None,
    difficulty_hint: int | None = None,
) -> list[dict]:
    """One batched LLM call: turn ALL matched cross-source fact pairs into
    multi-hop trivia questions in a single request.

    Output shape matches `reddit.generate_questions` exactly so the two lists
    can be merged and run through the same `validate_questions()` gate:
        {"category": ..., "question_text": ..., "correct_answer": ...,
         "wrong_answers": [...], "difficulty": 1|2|3, "source_snippet": ...}
    """
    if not pairs:
        return []

    lines = []
    for n, p in enumerate(pairs):
        fact_a, fact_b = p["fact_a"], p["fact_b"]
        lines.append(
            f"Pair {n} (bridging entity: {p['entity']}):\n"
            f"  Fact A [{fact_a['source_label']}]: {fact_a['fact_text']}\n"
            f"  Fact B [{fact_b['source_label']}]: {fact_b['fact_text']}"
        )
    user_prompt = f"Cross-source fact pairs about {name}:\n\n" + "\n\n".join(lines)

    hint_lines = []
    if category_hint:
        hint_lines.append(f"Prefer the category \"{category_hint}\" when it fits.")
    if difficulty_hint is not None:
        hint_lines.append(f"Prefer difficulty {difficulty_hint} when it fits.")
    hint_suffix = ("\n\n" + "\n".join(hint_lines)) if hint_lines else ""

    system_prompt = f"""You are a trivia question generator specializing in MULTI-HOP questions about a person named "{name}".

You are given pairs of facts, each fact pulled from a DIFFERENT source (e.g. one from a news article, one from a court record). For each pair that supports it, write ONE multi-hop trivia question that genuinely requires knowing BOTH facts to answer confidently — not a question answerable from either fact alone. Skip a pair if it can't support a real multi-hop question.

Each question must be in this JSON format (no markdown, no extra text):
{{"category": "history|entertainment|geography|science|sports|art_literature", "question_text": "...", "correct_answer": "...", "wrong_answers": ["...","...","..."], "difficulty": 1, "source_snippet": "..."}}

Rules:
- The question should reference or imply both facts (e.g. "The person mentioned in both a 2019 news article about X and a county court filing about Y ...").
- correct_answer and wrong_answers must be full sentences or specific facts
- wrong_answers must be plausible but clearly wrong
- difficulty 1=easy, 2=medium, 3=hard — multi-hop questions are usually 2 or 3
- source_snippet: briefly cite both source labels, e.g. "News + Court"
- Return ONLY the JSON array, no commentary
- If none of the pairs support a fair multi-hop question, return an empty JSON array: []{hint_suffix}"""

    LAST_FUSION_ERROR["reason"] = ""
    try:
        api_key = _api_key()
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{settings.litellm_base}/chat/completions",
                json={
                    "model": settings.litellm_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.8,
                    "max_tokens": settings.litellm_max_tokens,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            content = choice.get("message", {}).get("content") or ""
            questions = _extract_json_array(content)
            if not questions:
                LAST_FUSION_ERROR["reason"] = (
                    f"generate_fusion_questions: no usable questions parsed "
                    f"(finish_reason={choice.get('finish_reason')}, {len(content)} chars returned)"
                )
                return []
    except Exception as e:
        LAST_FUSION_ERROR["reason"] = f"generate_fusion_questions: {type(e).__name__}: {e}"
        return []

    out = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question_text", "")).strip()
        correct = str(q.get("correct_answer", "")).strip()
        if not text or not correct:
            continue
        wrong = q.get("wrong_answers") or []
        if not isinstance(wrong, list):
            continue
        cat = str(q.get("category", "")).strip()
        try:
            difficulty = int(q.get("difficulty", 2))
        except (TypeError, ValueError):
            difficulty = 2
        out.append({
            "category": cat if cat in CATEGORIES else "history",
            "question_text": text,
            "correct_answer": correct,
            "wrong_answers": [str(w).strip() for w in wrong][:3],
            "difficulty": difficulty if difficulty in (1, 2, 3) else 2,
            "source_snippet": str(q.get("source_snippet", ""))[:100],
            "is_fusion": True,
        })
    return out


_BRACKET_SPLIT_RE = re.compile(r"(?=^\[)", re.MULTILINE)


def split_raw_parts(raw_content: str) -> list[str]:
    """Fallback re-splitter for call sites that only have the already-joined
    `raw_content` string (no access to the original `raw_parts` list).

    Splits on lines that start a new bracket-labeled block, e.g. "[News results
    for: ...]" or "[Reddit u_spez]". This is best-effort — it can't perfectly
    recover the original per-scraper-call granularity (e.g. it can't tell two
    Reddit posts scraped in the same call apart), but it recovers source-block
    granularity, which is what fusion actually needs.
    """
    if not raw_content or not raw_content.strip():
        return []
    parts = [p.strip() for p in _BRACKET_SPLIT_RE.split(raw_content) if p.strip()]
    return parts if parts else [raw_content]


async def run_fact_fusion(profile_id: int, raw_parts: list[str], name: str) -> list[dict]:
    """Top-level orchestrator: extract -> match -> generate.

    Safe to call with raw_parts that don't have enough cross-source overlap —
    returns [] gracefully at any stage, never raises. This is what
    `_generate_questions_async` calls alongside the existing single-source
    `reddit.generate_questions`.
    """
    try:
        blocks = [b for b in (raw_parts or []) if b and b.strip()]
        if len(blocks) < _MIN_SOURCES_FOR_FUSION:
            return []

        facts = await extract_facts(blocks)
        if not facts:
            return []

        pairs = match_pairs(facts)
        if not pairs:
            return []

        questions = await generate_fusion_questions(profile_id, name, pairs)
        return questions
    except Exception as e:
        # Belt-and-suspenders: run_fact_fusion must never take down the scrape
        # flow it's bolted onto. Every internal step already catches its own
        # errors, but this guards against a bug in the orchestration itself.
        LAST_FUSION_ERROR["reason"] = f"run_fact_fusion: {type(e).__name__}: {e}"
        return []
