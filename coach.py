"""RAG Strategy Coach for the number guessing game.

Two features:
  1. Tiered hints (Nudge -> Range -> Best move). The CODE decides which level
     the player gets; the prompt for each level contains only the facts that
     level may reveal, retrieval only searches notes tagged for that level, and
     a validator rejects any answer that shares more than its level allows.
  2. Player review. Mistakes are found deterministically from the guess
     history (using only what the player already knew at each turn); the model
     only explains them in friendly words.

The secret is used only to derive the remaining range and is never placed in a
prompt (it appears in review facts only after the game is over). If the model
is unavailable or fails validation, a deterministic fallback is used.
"""
import math
import os
import re
from collections import Counter
from pathlib import Path

from logic_utils import DIFFICULTY_SETTINGS, check_guess

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_OUTPUT_TOKENS = 200

SYSTEM_INSTRUCTION = (
    "You are a friendly coach for a number guessing game. Use ONLY the facts "
    "and strategy notes you are given, follow the task exactly, and never "
    "invent numbers."
)

# ------------------------------------------------------------ hint ladder
# The only hard-coded part: how many levels, what they cost, what they may say.
HINT_LEVELS = {
    1: {"name": "Nudge", "cost": 0, "gives": "a thinking prompt, no numbers"},
    2: {"name": "Range", "cost": 5, "gives": "the numbers that are still possible"},
    3: {"name": "Best move", "cost": 15, "gives": "a specific number to guess"},
}
MAX_LEVEL = max(HINT_LEVELS)

# Lowest hint level at which each note may be retrieved. Notes that contain
# numbers are level 3 only; unknown notes default to the most restricted level.
NOTE_LEVELS = {
    "learning_from_feedback": 1, "narrowing_range": 1, "avoid_repeats": 1,
    "invalid_input": 1,
    "binary_search": 2, "few_attempts_left": 2, "small_range": 2,
    "first_guess": 3, "worst_case_guesses": 3, "scoring": 3,
    "difficulty_levels": 3,
}


def note_level(doc_id):
    return NOTE_LEVELS.get(doc_id, MAX_LEVEL)


def docs_for_level(docs, level):
    return [d for d in docs if note_level(d["id"]) <= level]


def next_level(uses):
    """Level of the next hint after `uses` hints on the current guess."""
    return min(uses + 1, MAX_LEVEL)


# ---------------------------------------------------------------- knowledge
def load_knowledge(directory=KNOWLEDGE_DIR):
    """Return a list of {"id", "text"} documents.

    Markdown files in `directory` plus a rules note generated from
    DIFFICULTY_SETTINGS so it can never disagree with the real game.
    """
    docs = []
    for path in sorted(Path(directory).glob("*.md")):
        docs.append({"id": path.stem, "text": path.read_text().strip()})
    levels = "; ".join(
        f"{name} uses the range {s['low']} to {s['high']} with {s['attempts']} attempts"
        for name, s in DIFFICULTY_SETTINGS.items()
    )
    docs.append({"id": "difficulty_levels", "text": f"# Difficulty levels\n{levels}."})
    return docs


# ---------------------------------------------------------------- retrieval
def _tokens(text):
    return re.findall(r"[a-z]+", text.lower())


def _numbers(text):
    return {int(n) for n in re.findall(r"\d+", text)}


def retrieve(query, docs, k=3):
    """Rank docs against the query with BM25 and return the top k (score > 0)."""
    doc_tokens = [_tokens(d["text"]) for d in docs]
    n = len(docs)
    avg_len = sum(len(t) for t in doc_tokens) / max(n, 1)
    k1, b = 1.5, 0.75
    scored = []
    for doc, toks in zip(docs, doc_tokens):
        score = 0.0
        for term, weight in Counter(_tokens(query)).items():
            tf = toks.count(term)
            if tf == 0:
                continue
            df = sum(1 for other in doc_tokens if term in other)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            score += weight * idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(toks) / avg_len))
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda pair: -pair[0])
    return [doc for _, doc in scored[:k]]


# ---------------------------------------------------------------- game state
def _is_guess(item):
    return isinstance(item, int) and not isinstance(item, bool)


def compute_state(history, secret, low, high, attempt_limit):
    """Derive what the player knows. Only valid (int) guesses count.

    Returns a dict with no secret in it.
    """
    cur_low, cur_high = low, high
    guesses = []
    last_outcome = None
    for item in history:
        if not _is_guess(item):
            continue  # invalid raw input is logged as a string by the app
        guesses.append(item)
        last_outcome = check_guess(item, secret)
        if last_outcome == "Too High":
            cur_high = min(cur_high, item - 1)
        elif last_outcome == "Too Low":
            cur_low = max(cur_low, item + 1)
    return {
        "low": low,
        "high": high,
        "cur_low": cur_low,
        "cur_high": cur_high,
        "guesses": guesses,
        "last_outcome": last_outcome,
        "attempts_left": max(attempt_limit - len(guesses), 0),
        "attempt_limit": attempt_limit,
    }


def build_query(state):
    """Turn the game state into a search query for the knowledge base."""
    parts = ["binary search middle"]
    size = state["cur_high"] - state["cur_low"] + 1
    if not state["guesses"]:
        parts.append("first guess opening")
    else:
        # mid-game: the player needs to know how feedback updates the bounds
        parts.append("narrowing update bound Too High Too Low")
    # Situation-specific phrases are repeated so they outweigh generic terms.
    if state["attempts_left"] <= 2:
        parts.append("few attempts left " * 3)
    if size <= 3:
        parts.append("small remaining range " * 3)
    if len(state["guesses"]) != len(set(state["guesses"])):
        parts.append("repeat repeated guesses " * 3)
    return " ".join(parts)


# ---------------------------------------------------------------- hints
def _middle(state):
    return (state["cur_low"] + state["cur_high"]) // 2


NUDGES = {
    None: ("Before you guess, picture every number that is still possible. "
           "Which guess would split them into two equal halves?"),
    "Too High": ("Your last guess was too high, so every larger number is ruled out. "
                 "Where does the possible range end now?"),
    "Too Low": ("Your last guess was too low, so every smaller number is ruled out. "
                "Where does the possible range start now?"),
}


def fallback_hint(state):
    """Deterministic level-3 hint (binary search). Always valid, needs no API."""
    lo, hi, mid = state["cur_low"], state["cur_high"], _middle(state)
    if lo == hi:
        text = f"Only one number is left: {lo}."
    else:
        text = (f"The secret is between {lo} and {hi}. "
                f"Guessing the middle, {mid}, removes half of the possibilities.")
    return text, mid


def fallback_for_level(state, level):
    """Deterministic hint for any level. Returns (text, suggested or None)."""
    if level == 1:
        return NUDGES.get(state["last_outcome"], NUDGES[None]), None
    if level == 2:
        lo, hi = state["cur_low"], state["cur_high"]
        if lo == hi:
            return f"Only one number is possible: {lo}.", None
        return f"The secret is between {lo} and {hi} (inclusive).", None
    return fallback_hint(state)


LEVEL_TASKS = {
    1: ("Task: write ONE short nudge (at most 25 words) that makes the player "
        "think about what their last feedback ruled out. Do NOT write any digits "
        "or numbers, and do NOT say which number to guess."),
    2: ("Task: in one or two short sentences, tell the player which numbers are "
        "still possible. Mention exactly the two range numbers given and no other "
        "numbers. Do NOT suggest a guess."),
    3: ("Task: write a hint of at most two short sentences that helps the player "
        "choose their next guess. The last line of your answer must be exactly "
        "'GUESS: <number>' where <number> is your suggested next guess, inside "
        "the remaining range."),
}


def build_prompt(state, docs, level=3):
    """Build the prompt. Each level includes ONLY the facts it may reveal."""
    if level == 1:
        feedback = state["last_outcome"] or "none yet"
        facts = (f"- Feedback on the player's last guess: {feedback}\n"
                 f"- Many numbers still possible: "
                 f"{'yes' if state['cur_high'] - state['cur_low'] > 3 else 'no'}")
    elif level == 2:
        facts = (f"- Remaining possible range: {state['cur_low']} to {state['cur_high']}")
    else:
        facts = (f"- Full range: {state['low']} to {state['high']}\n"
                 f"- Remaining possible range: {state['cur_low']} to {state['cur_high']}\n"
                 f"- Previous guesses: {state['guesses'] or 'none'}\n"
                 f"- Attempts left: {state['attempts_left']}")
    notes = "\n\n".join(d["text"] for d in docs)
    return f"{LEVEL_TASKS[level]}\n\nGame facts:\n{facts}\n\nStrategy notes:\n{notes}\n"


def parse_generation(text):
    """Split model output into (hint_text, suggested_guess or None)."""
    match = re.search(r"GUESS:\s*(-?\d+)\s*$", text.strip(), re.IGNORECASE)
    if not match:
        return text.strip(), None
    return text[: match.start()].strip(), int(match.group(1))


def validate_suggestion(text, suggested, state, level=3):
    """Guardrail: the answer may not share more than its level allows."""
    if not text:
        return False, "empty hint"
    found = _numbers(text)
    if level == 1:
        if found:
            return False, "nudge must not contain numbers"
        if suggested is not None:
            return False, "nudge must not suggest a guess"
        return True, ""
    if level == 2:
        if suggested is not None:
            return False, "range hint must not suggest a guess"
        if found != {state["cur_low"], state["cur_high"]}:
            return False, "range hint must state exactly the remaining range"
        return True, ""
    if suggested is None:
        return False, "no suggested guess"
    if not (state["cur_low"] <= suggested <= state["cur_high"]):
        return False, "suggestion outside remaining range"
    if suggested in state["guesses"] and state["cur_low"] != state["cur_high"]:
        return False, "suggestion repeats an earlier guess"
    allowed = {state["cur_low"], state["cur_high"], suggested, state["attempts_left"]}
    if not found <= allowed:
        return False, "hint mentions numbers it was not given"
    return True, ""


def gemini_generate(prompt, model=None):
    """Call Gemini. Raises on any problem; the caller falls back."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("no GEMINI_API_KEY set")
    from google import genai  # imported lazily so tests need no key
    from google.genai import types

    # Fail fast instead of hanging the UI: 15 s timeout, no silent retries.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=15000, retry_options=types.HttpRetryOptions(attempts=1)),
    )
    interaction = client.interactions.create(
        model=model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
        system_instruction=SYSTEM_INSTRUCTION,
        input=prompt,
        generation_config={"max_output_tokens": MAX_OUTPUT_TOKENS},
    )
    return interaction.output_text


def get_coach_hint(history, secret, low, high, attempt_limit, level=3,
                   docs=None, generator=gemini_generate, k=3):
    """Full pipeline for one hint. Never raises; always returns a usable hint.

    Returns a dict: text, suggested, sources (doc ids), used_llm, reason,
    level, cost. `generator(prompt) -> str` is injectable so tests need no
    network.
    """
    state = compute_state(history, secret, low, high, attempt_limit)
    all_docs = docs if docs is not None else load_knowledge()
    retrieved = retrieve(build_query(state), docs_for_level(all_docs, level), k=k)
    sources = [d["id"] for d in retrieved]
    meta = {"sources": sources, "level": level, "cost": HINT_LEVELS[level]["cost"]}

    reason = ""
    try:
        raw = generator(build_prompt(state, retrieved, level))
        text, suggested = parse_generation(raw)
        ok, reason = validate_suggestion(text, suggested, state, level)
        if ok:
            return {"text": text, "suggested": suggested, "used_llm": True,
                    "reason": "", **meta}
    except Exception as exc:  # network, quota, bad key, SDK change...
        reason = f"{type(exc).__name__}: {exc}"

    text, suggested = fallback_for_level(state, level)
    return {"text": text, "suggested": suggested, "used_llm": False,
            "reason": reason, **meta}


# ---------------------------------------------------------------- review
def optimal_guesses(low, high):
    """Worst-case guesses needed by perfect binary search on [low, high]."""
    return math.ceil(math.log2(high - low + 2))


def _worst_remaining(guess, lo, hi):
    """Most numbers that could remain after `guess` within [lo, hi]."""
    return max(guess - lo, hi - guess)


def analyze_guesses(history, secret, low, high):
    """Find mistakes using only what the player knew at each turn.

    kinds: "repeat" (guessed before), "outside_range" (already ruled out by
    earlier feedback), "off_middle" (inside the range but far from the middle,
    so it removes far fewer possibilities than a middle guess).
    """
    findings, seen = [], set()
    lo, hi, turn = low, high, 0
    for item in history:
        if not _is_guess(item):
            continue
        turn += 1
        size, mid = hi - lo + 1, (lo + hi) // 2
        kind = None
        if item in seen:
            kind = "repeat"
        elif not lo <= item <= hi:
            kind = "outside_range"
        elif (size > 3 and item != secret  # a winning guess is never a mistake
              and _worst_remaining(item, lo, hi) >= 1.5 * _worst_remaining(mid, lo, hi)):
            kind = "off_middle"
        if kind:
            findings.append({"turn": turn, "guess": item, "kind": kind,
                             "range": (lo, hi), "middle": mid})
        seen.add(item)
        outcome = check_guess(item, secret)
        if outcome == "Too High":
            hi = min(hi, item - 1)
        elif outcome == "Too Low":
            lo = max(lo, item + 1)
    return findings


def describe_finding(f):
    n, g, (lo, hi), mid = f["turn"], f["guess"], f["range"], f["middle"]
    if f["kind"] == "repeat":
        return f"Guess #{n} ({g}) repeated an earlier guess, which cannot teach you anything new."
    if f["kind"] == "outside_range":
        return (f"Guess #{n} ({g}) could not be the secret: you already knew "
                f"it was between {lo} and {hi}.")
    return (f"Guess #{n} ({g}) was far from the middle of {lo}-{hi}; "
            f"guessing near {mid} would have removed more possibilities.")


REVIEW_QUERY = {
    "repeat": "repeat repeated guesses",
    "outside_range": "narrowing update bound range",
    "off_middle": "binary search middle",
}

REVIEW_TASK = (
    "Task: write a friendly review of the player's guesses in at most four short "
    "sentences. Mention each finding briefly and say what to do differently. If "
    "there are no findings, praise the player. Use only numbers that appear in "
    "the facts below."
)


def fallback_review(summary, status):
    if summary["guesses"] == 0:
        return "Make a guess first, then I can review how you are doing."
    parts = [describe_finding(f) for f in summary["findings"]] or [
        "Every guess so far was inside the possible range and reasonably close to the middle."]
    if status == "won":
        parts.append(f"You won in {summary['guesses']} guesses; with perfect binary "
                     f"search the worst case is {summary['optimal']}.")
    elif status == "lost":
        parts.append(f"You ran out of attempts; perfect binary search needs at most "
                     f"{summary['optimal']} guesses.")
    return " ".join(parts)


def _review_allowed_numbers(summary, secret, status, attempt_limit):
    allowed = set(range(1, summary["guesses"] + 1)) | {summary["optimal"], attempt_limit}
    for f in summary["findings"]:
        allowed |= {f["guess"], f["middle"], *f["range"]}
    if status != "playing":
        allowed.add(secret)  # revealed anyway once the game is over
    return allowed


def get_review(history, secret, low, high, attempt_limit, status="playing",
               docs=None, generator=gemini_generate, k=3):
    """Review the player's guesses. Never raises.

    Returns a dict: text, findings, summary, sources, used_llm, reason.
    """
    findings = analyze_guesses(history, secret, low, high)
    n_guesses = sum(1 for h in history if _is_guess(h))
    summary = {"guesses": n_guesses, "optimal": optimal_guesses(low, high),
               "findings": findings}
    all_docs = docs if docs is not None else load_knowledge()
    query = " ".join(REVIEW_QUERY[f["kind"]] for f in findings) or "binary search middle narrowing"
    retrieved = retrieve(query, all_docs, k=k)
    base = {"findings": findings, "summary": summary,
            "sources": [d["id"] for d in retrieved]}

    reason = ""
    if n_guesses:
        result_line = {"won": f"won in {n_guesses} guesses", "lost": f"lost after {n_guesses} guesses"}.get(
            status, f"still playing after {n_guesses} guesses")
        facts = [f"- Game result: {result_line}",
                 f"- Best possible worst case with perfect play: {summary['optimal']} guesses"]
        facts += [f"- {describe_finding(f)}" for f in findings] or ["- No mistakes found."]
        prompt = (f"{REVIEW_TASK}\n\nGame facts:\n" + "\n".join(facts) +
                  "\n\nStrategy notes:\n" + "\n\n".join(d["text"] for d in retrieved) + "\n")
        try:
            text = generator(prompt).strip()
            if not text:
                reason = "empty review"
            elif _numbers(text) - _review_allowed_numbers(summary, secret, status, attempt_limit):
                reason = "review mentions numbers it was not given"
            else:
                return {"text": text, "used_llm": True, "reason": "", **base}
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
    return {"text": fallback_review(summary, status), "used_llm": False,
            "reason": reason, **base}
