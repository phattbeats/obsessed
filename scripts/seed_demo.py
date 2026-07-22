#!/usr/bin/env python3
"""
Seed a demo profile so a fresh clone is playable out of the box — no LLM,
no scraping, no network required.

Creates a consented profile for "Alex Delgado" (fictional), generates
questions from ~25 manual facts via the offline fallback generator, and
prints how to host a game.

Usage (from the repo root):
    python3 scripts/seed_demo.py

Idempotent: re-running replaces the previous demo profile's questions.
"""

import json
import sys
from pathlib import Path

# Allow running from anywhere: put the repo root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, Base, engine, Profile, Question  # noqa: E402
from app.services.generator import generate_from_manual  # noqa: E402

DEMO_NAME = "Alex Delgado"

# One fact per line; each must be >15 chars for generate_from_manual to use it.
DEMO_FACTS = """\
Alex once won a regional hot-sauce tasting championship without drinking any water.
Alex speaks fluent Portuguese learned entirely from telenovelas and karaoke nights.
Alex's first job was repairing arcade cabinets at a boardwalk in New Jersey.
Alex has climbed the tallest peak in three different countries, always in sandals.
Alex keeps a sourdough starter named Gerald that is older than their car.
Alex can recite the first 60 digits of pi but always forgets their own zip code.
Alex played bass in a ska band called The Filing Cabinets for six years.
Alex once got a standing ovation for a wedding toast delivered entirely in rhyme.
Alex collects vintage postcards of motels that no longer exist.
Alex taught a community college night class on the history of breakfast cereal.
Alex holds an unofficial record for most consecutive days wearing Hawaiian shirts.
Alex's cat, Admiral Biscuit, has more social media followers than Alex does.
Alex once biked from Portland to San Francisco fueled mostly by gas station burritos.
Alex can identify over forty bird species by their calls alone.
Alex won a chili cook-off using a secret ingredient revealed only as "regret".
Alex has seen the movie Jurassic Park in theaters more than twenty times.
Alex builds elaborate miniature dioramas of famous historical traffic jams.
Alex was briefly a hand model for a regional glove company catalog.
Alex learned to juggle while waiting in line for a music festival in 2011.
Alex's trivia weakness is geography, despite owning eleven different globes.
Alex once fixed a stranger's car engine using only a multitool and confidence.
Alex bakes a themed cake every year for the anniversary of the moon landing.
Alex ran a marathon dressed as a giant taco to raise money for the library.
Alex can solve a Rubik's cube behind their back on a good day.
Alex's karaoke signature song is a dramatic ballad version of a cartoon theme.
"""


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.name == DEMO_NAME).first()
        if p is None:
            p = Profile(name=DEMO_NAME, entity_type="person")
            db.add(p)
        p.bio = "Fictional demo guest of honor. Safe to delete."
        p.manual_facts = DEMO_FACTS
        p.consent_obtained = True
        db.commit()
        db.refresh(p)

        # Replace any previous demo questions so re-runs stay clean
        db.query(Question).filter(Question.profile_id == p.id).delete()

        questions = generate_from_manual(DEMO_FACTS, DEMO_NAME)
        for q in questions:
            db.add(Question(
                profile_id=p.id,
                category=q.get("category", "history"),
                question_text=q["question_text"],
                correct_answer=q["correct_answer"],
                wrong_answers=json.dumps(q.get("wrong_answers", [])),
                difficulty=q.get("difficulty", 1),
                source_snippet=q.get("source_snippet", "")[:500],
            ))
        p.question_count = len(questions)
        p.scrape_status = "complete"
        db.commit()

        print(f"Seeded demo profile: {DEMO_NAME}")
        print(f"  profile_id     = {p.id}")
        print(f"  question_count = {len(questions)}")
        print()
        print("Play it now:")
        print("  1. uvicorn app.main:app --reload")
        print("  2. Open http://localhost:8000 and click New Game,")
        print(f"     or: curl -X POST localhost:8000/api/games "
              f"-H 'Content-Type: application/json' -d '{{\"profile_id\": {p.id}}}'")
        print("  3. Share the room_code with players and start the game.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
