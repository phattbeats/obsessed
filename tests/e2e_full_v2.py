#!/usr/bin/env python3
"""
PHA-1027 — Full E2E test v2.

A live game, end to end, with NO mocks: the real FastAPI app, the real game
engine, real scoring. Questions are real (generated through the app's own
generator for one scenario; deterministically seeded fixtures for the
distribution scenarios so per-thing sums can be asserted exactly).

What this covers (maps 1:1 to the PHA-1027 acceptance list):
  1. Live game end to end — real questions, answers, scores.
  2. State captured at each step (lobby, things, game create, question, results)
     — emitted as a structured "full context" log (every API call + response).
  3. Full context — exact path, request body, status, and response for each call.
  4. Multi-thing scenarios — 1, 2, 5, and 10 things per game.
  5. Question-distribution verification — total == sum(per-thing num_questions).
  6. Edge cases — no questions (empty), >10 things, partial scrape, no consent.

Runs fully in-process (httpx ASGITransport) against an isolated SQLite DB, so
it needs no running server and never touches data/trivia.db.

Run:
    python3 tests/e2e_full_v2.py
    python3 tests/e2e_full_v2.py --artifacts out/   # write JSON + markdown report

Exit code 0 = all assertions passed.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── Isolate the DB BEFORE importing the app (mirrors tests/conftest.py) ─────────
_TMP = tempfile.mkdtemp(prefix="obsessed-e2ev2-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/trivia.db"
import app.database as _db  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

_db.DB_PATH = f"{_TMP}/trivia.db"
_db.engine = create_engine(
    f"sqlite:///{_db.DB_PATH}", connect_args={"check_same_thread": False}
)
_db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_db.engine)
_db.Base.metadata.create_all(bind=_db.engine)

from httpx import ASGITransport, AsyncClient  # noqa: E402
from app.main import app  # noqa: E402
from app.database import SessionLocal, Question  # noqa: E402

# ── Context log: every API call + response, for the "full context" artifact ─────
CONTEXT = []
ARTIFACT_DIR = None
for i, a in enumerate(sys.argv):
    if a == "--artifacts" and i + 1 < len(sys.argv):
        ARTIFACT_DIR = sys.argv[i + 1]


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def log(step, **kw):
    print(f"  · {step}" + (f" — {kw}" if kw else ""))


class Client:
    """Thin wrapper that records every call into CONTEXT."""

    def __init__(self, ac):
        self.ac = ac

    async def call(self, method, path, body=None, label=""):
        kw = {"json": body} if body is not None else {}
        r = await self.ac.request(method, path, **kw)
        try:
            data = r.json()
        except Exception:
            data = {"__raw": r.text[:300]}
        CONTEXT.append({
            "label": label, "method": method, "path": path,
            "request": body, "status": r.status_code, "response": data,
        })
        return r.status_code, data


# Map of question_text -> (correct_answer, [wrong_answers]) so we can answer
# correctly during play (the /question endpoint never reveals the answer).
ANSWER_KEY = {}


def seed_questions(profile_id, profile_name, n):
    """Insert n real, distinct trivia questions for a profile (fixture data).

    Each gets a unique question_text and a known correct answer so the player
    can submit a genuinely-correct answer and earn real points.
    """
    cats = ["history", "entertainment", "geography", "science", "sports", "art_literature"]
    db = SessionLocal()
    try:
        for i in range(n):
            qtext = f"[{profile_name}] Fact #{i + 1} — which is true?"
            correct = f"{profile_name} correct answer {i + 1}"
            wrongs = [f"{profile_name} wrong {i + 1}.{w}" for w in (1, 2, 3)]
            ANSWER_KEY[qtext] = (correct, wrongs)
            db.add(Question(
                profile_id=profile_id, category=cats[i % len(cats)],
                question_text=qtext, correct_answer=correct,
                wrong_answers=json.dumps(wrongs), difficulty=1,
            ))
        db.commit()
    finally:
        db.close()


async def make_profile(c, name, consent=True, facts=None, budget=50):
    body = {"name": name, "entity_type": "thing"}
    if facts:
        body["manual_facts"] = facts
        body["question_budget"] = budget
    st, data = await c.call("POST", "/api/profiles", body, f"create profile {name}")
    check(st == 200, f"create profile {name} → {st}: {data}")
    pid = data["id"]
    if consent:
        st, _ = await c.call("PUT", f"/api/profiles/{pid}", {"consent_obtained": True},
                             f"consent {name}")
        check(st == 200, f"consent {name} → {st}")
    return pid


async def play_full_game(c, room_code, p1, p2, expected_total):
    """Play every question to completion. p1 always answers correctly, p2 wrong.

    Returns (final_scores, questions_seen)."""
    # Start
    st, start = await c.call("POST", f"/api/games/{room_code}/start", None, "start game")
    check(st == 200, f"start → {st}: {start}")
    total = start["total_questions"]
    check(total == expected_total,
          f"DISTRIBUTION: total_questions={total}, expected sum={expected_total}")
    log("game started", total_questions=total)

    final_scores = []
    seen = 0
    for _ in range(total):
        st, q = await c.call("GET", f"/api/games/{room_code}/question", None,
                             f"get question {seen + 1}/{total}")
        check(st == 200, f"get question → {st}: {q}")
        check(len(q["options"]) >= 2, f"question has too few options: {q}")
        seen += 1
        qtext = q["question_text"]
        correct, wrongs = ANSWER_KEY.get(qtext, (q["options"][0], []))
        # p1 correct
        st, a1 = await c.call("POST", f"/api/games/{room_code}/answer",
                              {"player_id": p1, "answer_text": correct, "time_taken_ms": 2000},
                              f"p1 answers Q{seen}")
        check(st == 200 and a1["is_correct"] is True,
              f"p1 should be correct: {a1}")
        check(a1["points_earned"] > 0, f"correct answer earned 0 points: {a1}")
        # p2 wrong
        wrong_text = (wrongs or ["definitely wrong"])[0]
        st, a2 = await c.call("POST", f"/api/games/{room_code}/answer",
                              {"player_id": p2, "answer_text": wrong_text, "time_taken_ms": 4000},
                              f"p2 answers Q{seen}")
        check(st == 200 and a2["is_correct"] is False,
              f"p2 should be wrong: {a2}")
        check(a2["points_earned"] == 0, f"wrong answer earned points: {a2}")
        # capture standings before advancing (game still live in memory)
        st, scores = await c.call("GET", f"/api/games/{room_code}/scores", None,
                                  f"scores after Q{seen}")
        if st == 200:
            final_scores = scores
        # advance
        st, nx = await c.call("POST", f"/api/games/{room_code}/next", None, f"next after Q{seen}")
        check(st == 200, f"next → {st}: {nx}")

    check(seen == total, f"played {seen} questions, expected {total}")
    # Game should now be finished
    st, g = await c.call("GET", f"/api/games/{room_code}", None, "final game state")
    check(st == 200, f"final game state → {st}")
    check(g["status"] == "finished", f"game not finished after all questions: {g['status']}")
    return final_scores, seen


async def scenario_multi_thing(c, n_things, per_thing=None):
    """Create n profiles, seed pools, run a full game, verify distribution."""
    print(f"\n━━ Scenario: {n_things} thing(s) per game ━━")
    if per_thing is None:
        # vary counts so the sum is a non-trivial check, not n*constant
        per_thing = [3 + (i % 4) for i in range(n_things)]
    things = []
    expected_total = 0
    for i in range(n_things):
        pid = await make_profile(c, f"Thing{n_things}-{i + 1}")
        # seed MORE questions than requested so per-thing slicing is exercised
        pool = per_thing[i] + 5
        seed_questions(pid, f"Thing{n_things}-{i + 1}", pool)
        things.append({"profile_id": pid, "num_questions": per_thing[i]})
        expected_total += per_thing[i]
        log("profile + pool seeded", profile=pid, pool=pool, allotment=per_thing[i])

    st, game = await c.call("POST", "/api/games", {"things": things}, "create multi-thing game")
    check(st == 200, f"create game → {st}: {game}")
    rc = game["room_code"]
    check(len(game.get("things") or []) == n_things,
          f"game.things should have {n_things} entries: {game.get('things')}")
    log("game created", room_code=rc, things=len(game["things"]))

    # lobby: two players join
    p1, p2 = f"p1_{rc}", f"p2_{rc}"
    st, _ = await c.call("POST", f"/api/games/{rc}/join", {"player_id": p1, "player_name": "Ace"}, "join Ace")
    check(st == 200, f"join Ace → {st}")
    st, _ = await c.call("POST", f"/api/games/{rc}/join", {"player_id": p2, "player_name": "Buzz"}, "join Buzz")
    check(st == 200, f"join Buzz → {st}")
    st, lobby = await c.call("GET", f"/api/games/{rc}", None, "lobby state")
    check(len(lobby["players"]) == 2, f"lobby should show 2 players: {lobby['players']}")

    scores, seen = await play_full_game(c, rc, p1, p2, expected_total)
    # Ace answered all correct, Buzz all wrong → Ace must lead with > 0
    check(len(scores) == 2, f"final scores should list 2 players: {scores}")
    top = scores[0]
    check(top["player_name"] == "Ace" and top["score"] > 0,
          f"Ace should lead with points: {scores}")
    check(scores[1]["score"] == 0, f"Buzz should have 0 points: {scores}")
    print(f"  ✅ {n_things} things → played {seen} Qs, total==sum({'+'.join(map(str, per_thing))})"
          f"=={expected_total}; final: Ace={top['score']} Buzz={scores[1]['score']}")
    return expected_total


async def scenario_real_generation(c):
    """Prove question generation works end-to-end via the app's own generator.

    No LLM key is configured in this environment (BYOK), so we pin the LLM base
    to a dead address: the app then falls back to its real, deterministic
    rule-based generator — the same path a no-key deployment uses. This keeps
    the assertion stable instead of riding on a flaky live LLM proxy.
    """
    from app.config import settings
    settings.litellm_base = "http://127.0.0.1:9"
    settings.litellm_api_key = None
    print("\n━━ Scenario: real question generation (no seeding) ━━")
    facts = "\n".join([
        "The Great Barrier Reef is the world's largest coral reef system.",
        "Mount Everest is the highest mountain above sea level on Earth.",
        "The Amazon River discharges more water than any other river.",
        "The Sahara is the largest hot desert on the planet.",
        "The Pacific Ocean is the largest and deepest of Earth's oceans.",
        "Antarctica is the coldest continent and holds most of Earth's ice.",
    ])
    pid = await make_profile(c, "RealGen", facts=facts, budget=6)
    st, gen = await c.call("POST", f"/api/profiles/{pid}/generate", None, "generate questions")
    check(st == 200, f"generate → {st}: {gen}")
    qc = gen.get("question_count", 0)
    check(qc > 0, f"generation produced 0 questions (generator broken): {gen}")
    log("generated real questions", question_count=qc)
    print(f"  ✅ /generate produced {qc} real questions from manual facts")
    return pid, qc


async def scenario_edge_cases(c):
    print("\n━━ Scenario: edge cases ━━")
    results = {}

    # (a) consent NOT given → game create blocked
    pid_nc = await make_profile(c, "NoConsent", consent=False)
    seed_questions(pid_nc, "NoConsent", 3)
    st, data = await c.call("POST", "/api/games",
                            {"things": [{"profile_id": pid_nc, "num_questions": 3}]},
                            "create game w/o consent")
    check(st == 403, f"no-consent game should be 403, got {st}: {data}")
    results["consent_not_given"] = f"403 ✓ ({data.get('detail')})"

    # (b) >10 things → 400
    big = [{"profile_id": pid_nc, "num_questions": 1} for _ in range(11)]
    st, data = await c.call("POST", "/api/games", {"things": big}, "create game with 11 things")
    check(st == 400, f">10 things should be 400, got {st}: {data}")
    results["over_max_things"] = f"400 ✓ ({data.get('detail')})"

    # (c) empty / no questions → start blocked
    pid_empty = await make_profile(c, "Empty")  # consented, but zero questions
    st, game = await c.call("POST", "/api/games", {"profile_id": pid_empty}, "create empty game")
    check(st == 200, f"create empty game → {st}: {game}")
    rc = game["room_code"]
    await c.call("POST", f"/api/games/{rc}/join", {"player_id": "e1", "player_name": "Solo"}, "join empty")
    st, data = await c.call("POST", f"/api/games/{rc}/start", None, "start empty game")
    check(st == 400, f"empty start should be 400, got {st}: {data}")
    results["empty_no_questions"] = f"400 ✓ ({data.get('detail')})"

    # (d) partial scrape: a thing whose pool < num_questions → game runs with
    #     what's available; total == min(available, requested) summed.
    pid_full = await make_profile(c, "PartialFull")
    seed_questions(pid_full, "PartialFull", 6)          # asks 6, has 6
    pid_part = await make_profile(c, "PartialThin")
    seed_questions(pid_part, "PartialThin", 2)          # asks 6, has only 2
    things = [
        {"profile_id": pid_full, "num_questions": 6},
        {"profile_id": pid_part, "num_questions": 6},
    ]
    st, game = await c.call("POST", "/api/games", {"things": things}, "create partial-scrape game")
    check(st == 200, f"partial game create → {st}: {game}")
    rc = game["room_code"]
    p1, p2 = f"pf1_{rc}", f"pf2_{rc}"
    await c.call("POST", f"/api/games/{rc}/join", {"player_id": p1, "player_name": "Ace"}, "join")
    await c.call("POST", f"/api/games/{rc}/join", {"player_id": p2, "player_name": "Buzz"}, "join")
    # expected = 6 (full) + 2 (only 2 available) = 8
    scores, seen = await play_full_game(c, rc, p1, p2, expected_total=8)
    results["partial_scrape"] = f"played {seen} Qs (6 full + 2 available) ✓"
    print("  ✅ edge cases:")
    for k, v in results.items():
        print(f"     - {k}: {v}")
    return results


async def main():
    print("=" * 64)
    print("PHA-1027 — Full E2E test v2 (live game, in-process, no mocks)")
    print("=" * 64)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e") as ac:
        c = Client(ac)

        # Health + home
        st, h = await c.call("GET", "/api/health", None, "health")
        check(st == 200 and h.get("app") == "Obsessed", f"health failed: {h}")
        st, _ = await c.call("GET", "/", None, "home page")
        check(st == 200, f"home → {st}")
        print("  ✅ health + home OK")

        # Real generation
        await scenario_real_generation(c)

        # Multi-thing distribution: 1, 2, 5, 10
        sums = {}
        for n in (1, 2, 5, 10):
            sums[n] = await scenario_multi_thing(c, n)

        # Edge cases
        edge = await scenario_edge_cases(c)

    print("\n" + "=" * 64)
    print("✅ ALL E2E v2 ASSERTIONS PASSED")
    print("=" * 64)
    print("Distribution sums verified (total_questions == Σ num_questions):")
    for n, s in sums.items():
        print(f"   {n:>2} thing(s): Σ = {s}")
    print(f"API calls exercised: {len(CONTEXT)}")

    if ARTIFACT_DIR:
        outdir = Path(ARTIFACT_DIR)
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "e2e_v2_context.json").write_text(json.dumps(CONTEXT, indent=2))
        md = ["# PHA-1027 Full E2E v2 — run report", "",
              f"- API calls: {len(CONTEXT)}", "- All assertions passed.", "",
              "## Distribution (total_questions == Σ per-thing num_questions)"]
        for n, s in sums.items():
            md.append(f"- {n} thing(s): Σ = {s}")
        md += ["", "## Edge cases"]
        for k, v in edge.items():
            md.append(f"- {k}: {v}")
        (outdir / "e2e_v2_report.md").write_text("\n".join(md))
        print(f"Artifacts written to {outdir}/")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Fail as e:
        print(f"\n❌ E2E ASSERTION FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 UNEXPECTED ERROR: {e}")
        traceback.print_exc()
        sys.exit(2)
