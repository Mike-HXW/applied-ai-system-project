"""Reliability harness for the RAG coach.

Offline (default, no API key, deterministic): plays EVERY possible secret on
every difficulty by following the coach's suggested guess, and measures
win rate, guesses used, invalid suggestions, determinism, and whether
retrieval surfaces the right strategy note.

    python eval_harness.py            # offline report
    python eval_harness.py --live 20  # also try 20 real Gemini hints (uses API)
"""
import random
import sys
from pathlib import Path

from coach import (HINT_LEVELS, analyze_guesses, build_query, compute_state,
                   get_coach_hint, load_knowledge, retrieve, validate_suggestion)
from logic_utils import DIFFICULTY_SETTINGS, check_guess

REPORT_PATH = Path(__file__).parent / "eval_report.md"


def offline_generator(prompt):
    raise RuntimeError("offline evaluation: model disabled")


def play_game(secret, low, high, attempt_limit, generator=offline_generator, docs=None):
    """Follow the coach's suggestion each turn. Returns a result dict."""
    history, invalid, used_llm = [], 0, 0
    for _ in range(attempt_limit):
        hint = get_coach_hint(history, secret, low, high, attempt_limit,
                              docs=docs, generator=generator)
        used_llm += hint["used_llm"]
        state = compute_state(history, secret, low, high, attempt_limit)
        ok, _ = validate_suggestion(hint["text"], hint["suggested"], state)
        if not ok:
            invalid += 1
        guess = hint["suggested"]
        history.append(guess)
        if check_guess(guess, secret) == "Win":
            return {"won": True, "guesses": len(history), "invalid": invalid, "llm": used_llm}
    return {"won": False, "guesses": len(history), "invalid": invalid, "llm": used_llm}


def evaluate_difficulty(name, docs=None):
    s = DIFFICULTY_SETTINGS[name]
    results = [play_game(secret, s["low"], s["high"], s["attempts"], docs=docs)
               for secret in range(s["low"], s["high"] + 1)]
    n = len(results)
    return {
        "difficulty": name,
        "games": n,
        "win_rate": sum(r["won"] for r in results) / n,
        "avg_guesses": sum(r["guesses"] for r in results) / n,
        "max_guesses": max(r["guesses"] for r in results),
        "invalid_suggestions": sum(r["invalid"] for r in results),
    }


def check_determinism(docs=None):
    """Same game state must give the same hint every time."""
    mismatches = 0
    for name, s in DIFFICULTY_SETTINGS.items():
        for secret in range(s["low"], s["high"] + 1, 3):
            a = get_coach_hint([s["low"]], secret, s["low"], s["high"], s["attempts"],
                               docs=docs, generator=offline_generator)
            b = get_coach_hint([s["low"]], secret, s["low"], s["high"], s["attempts"],
                               docs=docs, generator=offline_generator)
            mismatches += (a["text"], a["suggested"], a["sources"]) != (b["text"], b["suggested"], b["sources"])
    return mismatches


# ---------------------------------------------------------------- mistake detection
def binary_history(secret, low, high, turns):
    """The first `turns` guesses of a perfect player (stops early on a win)."""
    history, lo, hi = [], low, high
    for _ in range(turns):
        guess = (lo + hi) // 2
        history.append(guess)
        outcome = check_guess(guess, secret)
        if outcome == "Win":
            break
        lo, hi = (guess + 1, hi) if outcome == "Too Low" else (lo, guess - 1)
    return history, lo, hi


def make_mistake(history, lo, hi, low, high, kind):
    """Return a guess that is exactly `kind` of mistake, or None if impossible."""
    if kind == "repeat":
        return history[-1] if history else None
    if kind == "outside_range":
        candidate = low if lo > low else high if hi < high else None
    else:  # off_middle
        candidate = lo if hi - lo + 1 > 3 else None
    return candidate if candidate is not None and candidate not in history else None


def evaluate_analysis():
    """Perfect players must get zero findings; injected mistakes must be caught."""
    perfect = false_positives = 0
    injected = detected = wrong_extra = 0
    per_kind = {"repeat": [0, 0], "outside_range": [0, 0], "off_middle": [0, 0]}
    for name, s in DIFFICULTY_SETTINGS.items():
        low, high, limit = s["low"], s["high"], s["attempts"]
        for secret in range(low, high + 1):
            history, _, _ = binary_history(secret, low, high, limit)
            perfect += 1
            false_positives += bool(analyze_guesses(history, secret, low, high))
            for kind in per_kind:
                for at_turn in (2, 3):
                    prefix, lo, hi = binary_history(secret, low, high, at_turn - 1)
                    if len(prefix) < at_turn - 1 or check_guess(prefix[-1], secret) == "Win":
                        continue  # game already over before the mistake
                    bad = make_mistake(prefix, lo, hi, low, high, kind)
                    if bad is None:
                        continue
                    found = analyze_guesses(prefix + [bad], secret, low, high)
                    injected += 1
                    per_kind[kind][0] += 1
                    hit = [f for f in found if f["turn"] == at_turn and f["kind"] == kind]
                    detected += bool(hit)
                    per_kind[kind][1] += bool(hit)
                    wrong_extra += len(found) - len(hit)
    return {"perfect_games": perfect, "false_positives": false_positives,
            "injected": injected, "detected": detected,
            "unexpected_findings": wrong_extra, "per_kind": per_kind}


# ---------------------------------------------------------------- hint-level gating
def sample_states(stride=7):
    """(history, secret, low, high, attempts) states a real game passes through."""
    for s in DIFFICULTY_SETTINGS.values():
        for secret in range(s["low"], s["high"] + 1, stride):
            for turns in range(0, 4):
                history, _, _ = binary_history(secret, s["low"], s["high"], turns)
                if history and check_guess(history[-1], secret) == "Win":
                    continue
                yield history, secret, s["low"], s["high"], s["attempts"]


def evaluate_level_gating():
    """Every level's hint must obey its rules, with and without a model.

    Also feeds a model that over-shares (always answers like level 3) and
    checks that levels 1 and 2 reject it and fall back.
    """
    checked = violations = overshare_tried = overshare_blocked = 0
    for history, secret, low, high, limit in sample_states():
        state = compute_state(history, secret, low, high, limit)
        for level in HINT_LEVELS:
            offline = get_coach_hint(history, secret, low, high, limit, level=level,
                                     generator=offline_generator)
            ok, _ = validate_suggestion(offline["text"], offline["suggested"], state, level)
            checked += 1
            violations += not ok

            mid = (state["cur_low"] + state["cur_high"]) // 2
            greedy = (f"Range {state['cur_low']} to {state['cur_high']}, "
                      f"try {mid}.\nGUESS: {mid}")
            result = get_coach_hint(history, secret, low, high, limit, level=level,
                                    generator=lambda prompt, g=greedy: g)
            if level < 3:
                overshare_tried += 1
                overshare_blocked += not result["used_llm"]
    return {"checked": checked, "violations": violations,
            "overshare_tried": overshare_tried, "overshare_blocked": overshare_blocked}


# Hand-labelled situations: (history, secret, low, high, attempts, expected note)
RETRIEVAL_CASES = [
    ([], 42, 1, 100, 8, "first_guess"),
    ([50, 25, 37, 43, 40, 41], 42, 1, 100, 8, "few_attempts_left"),
    ([50, 25, 37, 43, 40], 42, 1, 100, 8, "small_range"),
    ([10, 10], 17, 1, 20, 6, "avoid_repeats"),
    ([50], 30, 1, 100, 8, "narrowing_range"),
]


def evaluate_retrieval(docs=None, k=3):
    docs = docs if docs is not None else load_knowledge()
    rows, hits = [], 0
    for history, secret, low, high, attempts, expected in RETRIEVAL_CASES:
        state = compute_state(history, secret, low, high, attempts)
        got = [d["id"] for d in retrieve(build_query(state), docs, k=k)]
        hit = expected in got
        hits += hit
        rows.append((expected, got, hit))
    return hits / len(RETRIEVAL_CASES), rows


def evaluate_live(n, seed=0):
    """Try n real model hints on random mid-game states. Costs API tokens."""
    from coach import gemini_generate
    rng = random.Random(seed)
    used, fallbacks, reasons = 0, 0, []
    for i in range(n):
        name = rng.choice(list(DIFFICULTY_SETTINGS))
        s = DIFFICULTY_SETTINGS[name]
        secret = rng.randint(s["low"], s["high"])
        history, lo, hi = [], s["low"], s["high"]
        for _ in range(rng.randint(0, 3)):
            if lo >= hi:
                break
            g = rng.randint(lo, hi)
            history.append(g)
            outcome = check_guess(g, secret)
            lo, hi = (g + 1, hi) if outcome == "Too Low" else (lo, g - 1) if outcome == "Too High" else (lo, hi)
        level = rng.choice(list(HINT_LEVELS))
        hint = get_coach_hint(history, secret, s["low"], s["high"], s["attempts"],
                              level=level, generator=gemini_generate)
        if hint["used_llm"]:
            used += 1
        else:
            fallbacks += 1
            reasons.append(hint["reason"])
        print(f"  live {i + 1}/{n} (level {level}): {'AI ok' if hint['used_llm'] else 'fallback - ' + hint['reason'][:120]}",
              flush=True)
    return {"n": n, "llm_valid": used, "fallbacks": fallbacks, "reasons": sorted(set(reasons))[:3]}


def build_report(live=None):
    lines = ["# Coach reliability report", "", "## Simulated games (every secret, coach followed)", "",
             "| Difficulty | Games | Win rate | Avg guesses | Max guesses | Invalid suggestions |",
             "|---|---|---|---|---|---|"]
    for name in DIFFICULTY_SETTINGS:
        r = evaluate_difficulty(name)
        lines.append(f"| {r['difficulty']} | {r['games']} | {r['win_rate']:.0%} | "
                     f"{r['avg_guesses']:.2f} | {r['max_guesses']} | {r['invalid_suggestions']} |")
    lines += ["", f"## Determinism: {check_determinism()} mismatches across repeated runs", "",
              "## Retrieval relevance (expected note in top 3)", ""]
    score, rows = evaluate_retrieval()
    lines.append(f"Hit rate: {score:.0%}\n")
    for expected, got, hit in rows:
        lines.append(f"- {'PASS' if hit else 'FAIL'}: expected `{expected}`, got {got}")
    analysis = evaluate_analysis()
    gating = evaluate_level_gating()
    lines += ["", "## Mistake detection (review feature)", "",
              f"- Perfect binary-search players flagged: {analysis['false_positives']} of "
              f"{analysis['perfect_games']} games (target 0)",
              f"- Injected mistakes detected: {analysis['detected']} of {analysis['injected']}",
              f"- Unexpected extra findings: {analysis['unexpected_findings']} (target 0)"]
    for kind, (total, hit) in analysis["per_kind"].items():
        lines.append(f"  - {kind}: {hit}/{total}")
    lines += ["", "## Hint-level gating", "",
              f"- Fallback hints that obey their level's rules: "
              f"{gating['checked'] - gating['violations']} of {gating['checked']}",
              f"- Over-sharing model answers blocked at levels 1-2: "
              f"{gating['overshare_blocked']} of {gating['overshare_tried']}"]
    if live:
        lines += ["", "## Live model sample", "",
                  f"{live['llm_valid']}/{live['n']} hints were AI-written and passed the guardrail; "
                  f"{live['fallbacks']} fell back to the deterministic hint."]
        for reason in live["reasons"]:
            lines.append(f"- fallback reason: {reason}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    live_result = None
    if "--live" in sys.argv:
        live_result = evaluate_live(int(sys.argv[sys.argv.index("--live") + 1]))
    report = build_report(live_result)
    REPORT_PATH.write_text(report)
    print(report)
    print(f"Saved to {REPORT_PATH}")
