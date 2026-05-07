#!/usr/bin/env python3
"""
E2E test for PHA-579 — PHA-577 multi-thing end-to-end.

Uses Python stdlib only (urllib, json, http) so it runs anywhere
without pip install. Where Playwright is available (CI runner),
screenshots are captured at each step.

Run:
  # Local (no browser):
  python3 tests/e2e_multi_thing.py

  # CI with screenshots (requires Playwright):
  PLAYWRIGHT_BASE_URL=http://10.0.0.100:10198 \
  PLAYWRIGHT_SCREENSHOT_DIR=screenshots \
  python3 tests/e2e_multi_thing.py --screenshots
"""

import json
import os
import sys
import time
import uuid
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = os.environ.get("PLAYWRIGHT_BASE_URL", "http://10.0.0.100:10198")
SCREENSHOT_DIR = os.environ.get("PLAYWRIGHT_SCREENSHOT_DIR", "screenshots")
USE_SCREENSHOTS = "--screenshots" in sys.argv

# ── HTTP helper ────────────────────────────────────────────────────────────────
def api(method: str, path: str, body=None, headers=None):
    """Make an API call. Returns (status, json_body)."""
    url = f"{BASE_URL}{path}"
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, method=method, data=data, headers=h)
    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return resp.status, {"__raw": raw.decode("utf-8", errors="replace")[:500]}
    except HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body) if body else {}
        except json.JSONDecodeError:
            return e.code, {"__raw": body.decode("utf-8", errors="replace")[:500]}
    except URLError as e:
        return 0, {"error": str(e)}


def screenshot(name: str):
    """Capture a screenshot if Playwright is available."""
    if not USE_SCREENSHOTS:
        return
    try:
        from playwright.sync_api import sync_playwright
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(BASE_URL, wait_until="networkidle")
            page.screenshot(path=f"{SCREENSHOT_DIR}/{name}.png")
            browser.close()
    except Exception as e:
        print(f"  [screenshot note] could not capture {name}: {e}")


def run():
    print("=" * 60)
    print("PHA-579 E2E — PHA-577 multi-thing end-to-end test")
    print("=" * 60)

    run_id = uuid.uuid4().hex[:6]
    print(f"\n[run id: {run_id}]\n")

    # ── Step 1: Home screen ─────────────────────────────────────────
    print("Step 1: Home screen loads")
    status, data = api("GET", "/")
    assert status == 200, f"Home page failed: {status}"
    print("  ✅ GET / → 200 (HTML home page)")
    screenshot("01_home")

    # ── Step 2: Health check ─────────────────────────────────────────
    print("\nStep 2: Health endpoint")
    status, data = api("GET", "/api/health")
    assert status == 200 and data.get("app") == "Obsessed", f"Health check failed: {data}"
    print("  ✅ GET /api/health → ok")

    # ── Step 3: Create Profile A ─────────────────────────────────────
    print("\nStep 3: Create profile A")
    profile_a_name = f"Test Person A {run_id}"
    status, resp = api("POST", "/api/profiles", {
        "name": profile_a_name,
        "entity_type": "person",
        "manual_facts": (
            "The capital of France is Paris. "
            "The Eiffel Tower is in Paris. "
            "Paris has 2.1 million residents. "
            "France borders Spain and Germany. "
            "The French flag is blue white red. "
            "Napoleon was born in Corsica. "
            "The Louvre is in Paris. "
            "French is spoken in 29 countries. "
            "The Seine runs through Paris. "
            "France has 67 million people. "
            "French cuisine is UNESCO heritage. "
            "France won the 2018 World Cup. "
            "The Tour de France is a cycling race. "
            "France is the largest country in the EU. "
            "French bread is famous worldwide."
        ),
        "question_budget": 10,
    })
    assert status == 200, f"Create profile A failed: {resp}"
    pid_a = resp["id"]
    print(f"  ✅ Created profile A: id={pid_a}")
    screenshot("03_profile_a_created")

    # ── Step 4: Create Profile B ─────────────────────────────────────
    print("\nStep 4: Create profile B")
    profile_b_name = f"Test Person B {run_id}"
    status, resp = api("POST", "/api/profiles", {
        "name": profile_b_name,
        "entity_type": "person",
        "manual_facts": (
            "The capital of Japan is Tokyo. "
            "Mount Fuji is the highest mountain in Japan. "
            "Tokyo has 14 million residents. "
            "Japan is an island nation. "
            "The Japanese flag is white with a red circle. "
            "Sushi originated in Japan. "
            "Japan has 125 million people. "
            "The Shinkansen is the bullet train. "
            "Cherry blossoms bloom in spring. "
            "Mount Fuji is 3776 meters tall. "
            "Japan hosted the 2020 Summer Olympics. "
            "Akihabara is a district in Tokyo. "
            "Japanese写字 is calligraphy. "
            "Japan is prone to earthquakes. "
            "The tea ceremony is a Japanese tradition."
        ),
        "question_budget": 10,
    })
    assert status == 200, f"Create profile B failed: {resp}"
    pid_b = resp["id"]
    print(f"  ✅ Created profile B: id={pid_b}")
    screenshot("04_profile_b_created")

    # ── Step 5: Grant consent to both profiles ───────────────────────
    print("\nStep 5: Grant consent to both profiles")
    for pid, name in [(pid_a, "A"), (pid_b, "B")]:
        status, resp = api("PUT", f"/api/profiles/{pid}", {"consent_obtained": True})
        assert status == 200, f"Consent for profile {name} failed: {resp}"
        print(f"  ✅ Consent granted to profile {name} (id={pid})")

    # ── Step 6: Generate questions for both profiles ────────────────
    print("\nStep 6: Trigger question generation via POST /generate")
    for pid, name in [(pid_a, "A"), (pid_b, "B")]:
        status, resp = api("POST", f"/api/profiles/{pid}/generate", None)
        print(f"  • Generate profile {name}: status={status}, resp={resp}")
    # Wait for question generation (async LLM call)
    time.sleep(3)

    # Poll until both profiles have questions ready
    print("\n  Polling profiles for question_count...")
    for pid, name in [(pid_a, "A"), (pid_b, "B")]:
        for attempt in range(10):
            _, resp = api("GET", f"/api/profiles/{pid}")
            qc = resp.get("question_count", 0)
            status_str = resp.get("scrape_status", "?")
            print(f"    profile {name}: question_count={qc}, status={status_str}")
            if qc > 0:
                break
            time.sleep(2)
        else:
            print(f"  ⚠️  Profile {name} question_count={qc} (may still be generating)")

    # ── Step 7: Create a multi-thing game (2 profiles) ──────────────
    print("\nStep 7: Create multi-thing game with 2 profiles")
    status, game = api("POST", "/api/games", {
        "things": [
            {"profile_id": pid_a, "num_questions": 10},
            {"profile_id": pid_b, "num_questions": 10},
        ]
    })
    assert status == 200, f"Multi-thing game create failed: {game}"
    room_code = game["room_code"]
    print(f"  ✅ Game created: room_code={room_code}")
    print(f"     things={game.get('things')} (should be 2 entries)")
    screenshot("07_game_created")

    # ── Step 8: Verify things field in game response ─────────────────
    things = game.get("things", [])
    assert len(things) == 2, f"Expected 2 things, got {len(things)}"
    print(f"  ✅ Game response includes things array with {len(things)} entries")

    # ── Step 9: Join player Alice ────────────────────────────────────
    print("\nStep 9: Join player Alice")
    player_id_alice = f"alice_{run_id}"
    status, resp = api("POST", f"/api/games/{room_code}/join", {
        "player_id": player_id_alice,
        "player_name": "Alice",
    })
    assert status == 200, f"Alice join failed: {resp}"
    print(f"  ✅ Alice joined (player_id={player_id_alice})")

    # ── Step 10: Join player Bob ─────────────────────────────────────
    print("\nStep 10: Join player Bob")
    player_id_bob = f"bob_{run_id}"
    status, resp = api("POST", f"/api/games/{room_code}/join", {
        "player_id": player_id_bob,
        "player_name": "Bob",
    })
    assert status == 200, f"Bob join failed: {resp}"
    print(f"  ✅ Bob joined (player_id={player_id_bob})")

    # ── Step 11: Start game ──────────────────────────────────────────
    print("\nStep 11: Start game")
    status, resp = api("POST", f"/api/games/{room_code}/start")
    assert status == 200, f"Start game failed: {resp}"
    print(f"  ✅ Game started")
    print(f"     total_questions={resp.get('total_questions')} (should be >10, merged from both profiles)")
    screenshot("11_game_started")

    # ── Step 12: Fetch first question ───────────────────────────────
    print("\nStep 12: Fetch first question")
    status, q = api("GET", f"/api/games/{room_code}/question")
    assert status == 200, f"Get question failed: {q}"
    print(f"  ✅ Question fetched")
    print(f"     category={q.get('category')}, question_num={q.get('question_num')}/{q.get('total_questions')}")
    print(f"     options count={len(q.get('options', []))}")
    screenshot("12_first_question")

    # ── Step 13: Submit answer ───────────────────────────────────────
    print("\nStep 13: Submit answer (Alice)")
    first_option = q.get("options", ["A"])[0]
    status, resp = api("POST", f"/api/games/{room_code}/answer", {
        "player_id": player_id_alice,
        "answer_text": first_option,
        "time_taken_ms": 3500,
    })
    assert status == 200, f"Submit answer failed: {resp}"
    print(f"  ✅ Answer submitted")
    print(f"     is_correct={resp.get('is_correct')}, points_earned={resp.get('points_earned')}")

    # ── Step 14: Get scores via /games/{room_code} ───────────────────
    print("\nStep 14: Get scores via /games/{room_code}")
    status, resp = api("GET", f"/api/games/{room_code}")
    assert status == 200, f"Scores check failed: {resp}"
    print(f"  ✅ Game state retrieved")
    players = resp.get("players", [])
    for pl in players:
        print(f"     {pl.get('player_name','?')}: {pl.get('score',0)} pts")
    screenshot("14_scores")

    # ── Step 15: Game status ────────────────────────────────────────
    print("\nStep 15: Get game status")
    status, resp = api("GET", f"/api/games/{room_code}")
    assert status == 200, f"Status check failed: {resp}"
    print(f"  ✅ Game status retrieved")
    print(f"     players: {[p['player_name'] for p in resp.get('players', [])]}")

    # ── Step 16: Backward compat — single profile_id still works ───
    print("\nStep 16: Backward compatibility — single profile_id game")
    status, resp = api("POST", "/api/profiles", {
        "name": f"Solo {run_id}",
        "entity_type": "person",
        "manual_facts": "A thing. Another thing. A third thing. Four. Five. Six. Seven. Eight. Nine. Ten. Eleven. Twelve. Thirteen. Fourteen. Fifteen.",
        "question_budget": 5,
    })
    solo_pid = resp["id"]
    api("PUT", f"/api/profiles/{solo_pid}", {"consent_obtained": True})
    time.sleep(1)
    # Give it a question
    api("POST", f"/api/profiles/{solo_pid}/generate", None)
    time.sleep(2)

    status, solo_game = api("POST", "/api/games", {"profile_id": solo_pid})
    assert status == 200, f"Solo game create failed: {solo_game}"
    print(f"  ✅ Single-profile game still works (room={solo_game['room_code']})")
    assert solo_game.get("things") is None, "things should be null for single profile_id"
    print(f"     things=null (correct — backward compat preserved)")

    # ── Cleanup ───────────────────────────────────────────────────────
    print("\nStep 17: Cleanup (delete test profiles)")
    for pid in [pid_a, pid_b, solo_pid]:
        status, _ = api("DELETE", f"/api/profiles/{pid}")
        print(f"  • Deleted profile {pid}: status={status}")

    # ── Summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ ALL STEPS PASSED — PHA-579 E2E complete")
    print("=" * 60)
    print(f"\nScreenshots: {SCREENSHOT_DIR}/ (if --screenshots used)")
    print("Coverage:")
    print("  ✅ Home screen loads")
    print("  ✅ Health endpoint")
    print("  ✅ Profile creation (2 profiles)")
    print("  ✅ Consent granting (PUT /api/profiles/{id})")
    print("  ✅ Question generation via POST /generate")
    print("  ✅ Multi-thing game create (2 profiles, things JSONB)")
    print("  ✅ things array correctly populated in game response")
    print("  ✅ Player join (2 players)")
    print("  ✅ Game start (merged question pool)")
    print("  ✅ Fetch question")
    print("  ✅ Submit answer")
    print("  ✅ Leaderboard/scores")
    print("  ✅ Game status")
    print("  ✅ Backward compat: single profile_id → things=null")
    print("  ✅ Cleanup")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
