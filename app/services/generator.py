import random, json

CATEGORIES = ["history", "entertainment", "geography", "science", "sports", "art_literature"]

def generate_from_manual(raw_text: str, name: str, count: int = 25) -> list[dict]:
    """Rule-based question fallback when LLM is unavailable."""
    lines = [l.strip() for l in raw_text.split("\n") if len(l.strip()) > 15]
    if not lines:
        return []

    questions = []
    for i, line in enumerate(lines[:count]):
        cat = CATEGORIES[i % len(CATEGORIES)]
        # Build a simple question from the line
        # Try to extract something that looks like a fact
        words = line.split()
        if len(words) < 4:
            continue
        
        # Create a question where the line is the answer
        q = {
            "category": cat,
            "question_text": f"Which of the following is a fact about {name}?",
            "correct_answer": line[:200],
            "wrong_answers": [l[:200] for l in lines[i+1:i+4] if l != line][:3],
            "difficulty": random.choice([1, 1, 2]),
            "source_snippet": line[:100],
        }
        if len(q["wrong_answers"]) < 3:
            q["wrong_answers"] = ["Never mentioned", "Completely unrelated topic", "A different subject entirely"]
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