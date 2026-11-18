"""Reproduce the README sample interactions with no API key and no network.

    python demo.py

Uses the real coach code with the model call disabled, so the wording is the
built-in fallback and the output is identical on every run. With a Gemini key
the app produces the same kind of output, written by the model.
"""
from coach import HINT_LEVELS, MAX_LEVEL, get_coach_hint, get_review


def no_model(prompt):
    raise RuntimeError("model disabled for a reproducible demo")


def cost(c):
    return "free" if c == 0 else f"-{c} pts"


def show_hint(hint):
    step = HINT_LEVELS[hint["level"]]
    print(f"OUTPUT  Hint {hint['level']}/{MAX_LEVEL} · {step['name']}: {hint['text']}")
    if hint["suggested"] is not None:
        print(f"        Suggested guess: {hint['suggested']}")
    source = "AI-written" if hint["used_llm"] else "built-in fallback"
    print(f"        Based on: {', '.join(hint['sources'])} · {source} · cost: {cost(hint['cost'])}")
    if hint["reason"] and "model disabled" not in hint["reason"]:  # show only real rejections
        print(f"        Reason AI answer not used: {hint['reason']}")


SECRET, LOW, HIGH, LIMIT = 37, 1, 100, 8

print("=== Example 1: the hint ladder ===")
print("INPUT   Normal (1-100), secret 37. Guesses so far: 50 (too high). Ask Coach x3")
for level in (1, 2, 3):
    show_hint(get_coach_hint([50], SECRET, LOW, HIGH, LIMIT, level=level, generator=no_model))

print("\n=== Example 2: review my guesses ===")
print("INPUT   Guesses: 50, 90, 90, 45. Click 'Review my guesses'")
review = get_review([50, 90, 90, 45], SECRET, LOW, HIGH, LIMIT, generator=no_model)
print(f"OUTPUT  {review['text']}")
print(f"        Based on: {', '.join(review['sources'])}")
print("INPUT   Guesses: 50, 25, 37 (win)")
review = get_review([50, 25, 37], SECRET, LOW, HIGH, LIMIT, status="won", generator=no_model)
print(f"OUTPUT  {review['text']}")

print("\n=== Example 3: the guardrail rejects an over-sharing model answer ===")
draft = "Think about bounds, maybe 25."
print("INPUT   Guesses so far: 50. Ask Coach (Nudge). Model drafts: " + repr(draft))
show_hint(get_coach_hint([50], SECRET, LOW, HIGH, LIMIT, level=1, generator=lambda p: draft))
