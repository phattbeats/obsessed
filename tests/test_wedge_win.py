"""
Tests for PHA-1335: enforce wedge win condition (first to 6 wedges wins).

SPEC: "first to complete all 6 category wedges, OR highest score after
question set exhausted."

These tests cover the GameState wedge logic directly (no app boot needed).
The /answer and /next route integration is exercised in test_smoke.py once
the full FastAPI app is importable in the test environment.
"""

import pytest

from app.services.game_engine import GameState, PlayerState, TriviaQuestion


ALL_CATEGORIES = {"history", "entertainment", "geography", "science", "sports", "art_literature"}


def _new_gs(categories=None):
    """Build a 50-question game with one question per category in the supplied order."""
    cats = list(categories or ALL_CATEGORIES)
    gs = GameState(room_code="T", profile_id=1, total_q=50)
    gs.questions = [
        TriviaQuestion(category=cats[i % len(cats)], question_text=f"q{i}",
                       correct_answer="a", wrong_answers=["b", "c", "d"])
        for i in range(50)
    ]
    gs.current_q = 0
    return gs


def _player(pid: str, name: str) -> PlayerState:
    p = PlayerState(player_id=pid, player_name=name)
    p.is_active = True
    return p


# ── all_wedges_earned() ────────────────────────────────────────────────────────


def test_all_wedges_earned_false_initially():
    gs = _new_gs()
    gs.players["a"] = _player("a", "Alice")
    gs.players["b"] = _player("b", "Bob")
    assert gs.all_wedges_earned() is False


def test_all_wedges_earned_true_when_player_has_six():
    gs = _new_gs()
    alice = _player("a", "Alice")
    alice.wedges = set(ALL_CATEGORIES)
    gs.players["a"] = alice
    assert gs.all_wedges_earned() is True


def test_all_wedges_earned_requires_six_distinct_categories():
    """Five categories isn't enough; needs exactly six distinct category names."""
    gs = _new_gs()
    alice = _player("a", "Alice")
    alice.wedges = {"history", "entertainment", "geography", "science", "sports"}
    gs.players["a"] = alice
    assert gs.all_wedges_earned() is False


def test_wedge_winner_excludes_inactive_players():
    """wedge_winner() filters by is_active — left-game players can't win."""
    gs = _new_gs()
    alice = _player("a", "Alice")
    alice.is_active = False
    alice.wedges = set(ALL_CATEGORIES)
    gs.players["a"] = alice

    bob = _player("b", "Bob")
    bob.wedges = {"history"}
    gs.players["b"] = bob

    assert gs.wedge_winner() is None


# ── wedge_winner() ─────────────────────────────────────────────────────────────


def test_wedge_winner_returns_player_with_all_six():
    gs = _new_gs()
    alice = _player("a", "Alice")
    alice.wedges = set(ALL_CATEGORIES)
    gs.players["a"] = alice
    gs.players["b"] = _player("b", "Bob")
    assert gs.wedge_winner() is alice


def test_wedge_winner_none_when_no_player_has_six():
    gs = _new_gs()
    alice = _player("a", "Alice")
    alice.wedges = {"history"}
    gs.players["a"] = alice
    gs.players["b"] = _player("b", "Bob")
    assert gs.wedge_winner() is None


# ── winner() — wedge preferred over score ─────────────────────────────────────


def test_winner_prefers_wedge_complete_over_higher_score():
    """Bug: winner() previously returned max score even if opponent had all 6 wedges.

    PHA-1335 fix: winner() returns the wedge-complete player even if their
    raw score is lower than another player's.
    """
    gs = _new_gs()
    alice = _player("a", "Alice")
    alice.wedges = set(ALL_CATEGORIES)  # all 6 wedges
    alice.score = 1000
    gs.players["a"] = alice

    bob = _player("b", "Bob")
    bob.wedges = {"history", "entertainment"}  # only 2 wedges
    bob.score = 9999  # higher score
    gs.players["b"] = bob

    assert gs.winner() is alice


def test_winner_falls_back_to_max_score_when_no_wedge_complete():
    """If no one has all 6 wedges, winner() returns the highest-scoring player."""
    gs = _new_gs()
    alice = _player("a", "Alice")
    alice.wedges = {"history", "entertainment", "geography"}
    alice.score = 1000
    gs.players["a"] = alice

    bob = _player("b", "Bob")
    bob.wedges = {"history"}
    bob.score = 5000
    gs.players["b"] = bob

    assert gs.winner() is bob


def test_winner_skips_inactive_players():
    gs = _new_gs()
    alice = _player("a", "Alice")
    alice.is_active = False
    alice.score = 9999
    gs.players["a"] = alice

    bob = _player("b", "Bob")
    bob.score = 100
    gs.players["b"] = bob

    assert gs.winner() is bob


def test_winner_none_when_no_active_players():
    gs = _new_gs()
    alice = _player("a", "Alice")
    alice.is_active = False
    gs.players["a"] = alice
    assert gs.winner() is None


# ── record_answer() wedge accumulation + win trigger ─────────────────────────


def test_record_answer_adds_wedge_for_new_category():
    gs = _new_gs(categories=["history", "entertainment"])
    gs.players["a"] = _player("a", "Alice")
    # Q0 is history
    correct, pts = gs.record_answer("a", "a", 1000)
    assert correct is True
    assert "history" in gs.players["a"].wedges
    assert gs.all_wedges_earned() is False


def test_record_answer_does_not_duplicate_wedge():
    """Answering correctly in the same category twice doesn't double-count."""
    gs = _new_gs(categories=["history", "history"])
    gs.players["a"] = _player("a", "Alice")
    gs.record_answer("a", "a", 1000)
    gs.next_question()
    gs.record_answer("a", "a", 1000)
    assert gs.players["a"].wedges == {"history"}


def test_six_correct_answers_across_six_categories_triggers_wedge_win():
    """The bug PHA-1335 fixes: 6 correctly-answered questions should end the game.

    GameState doesn't auto-set status='finished' — the route does — but
    all_wedges_earned() must flip to True at the moment the 6th wedge is
    earned so the route can detect it.
    """
    gs = _new_gs(categories=list(ALL_CATEGORIES))
    gs.players["a"] = _player("a", "Alice")
    gs.players["b"] = _player("b", "Bob")

    for i, cat in enumerate(ALL_CATEGORIES):
        # Alice gets the question right
        gs.record_answer("a", "a", 1000)
        # Bob answers wrong
        gs.record_answer("b", "wrong", 1000)
        if i < 5:
            assert gs.all_wedges_earned() is False, \
                f"all_wedges_earned() should be False after {i+1} wedges"
            gs.next_question()

    # After 6th answer: Alice has all 6 wedges.
    assert gs.players["a"].wedges == ALL_CATEGORIES
    assert gs.all_wedges_earned() is True
    assert gs.wedge_winner() is gs.players["a"]
    assert gs.winner() is gs.players["a"]


def test_wrong_answers_do_not_award_wedges():
    gs = _new_gs(categories=list(ALL_CATEGORIES))
    gs.players["a"] = _player("a", "Alice")

    for cat in ALL_CATEGORIES:
        gs.record_answer("a", "wrong", 1000)
        gs.next_question()

    assert gs.players["a"].wedges == set()
    assert gs.all_wedges_earned() is False