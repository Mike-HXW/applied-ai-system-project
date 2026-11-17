import pytest

import re

from coach import (HINT_LEVELS, NOTE_LEVELS, analyze_guesses, compute_state,
                   docs_for_level, get_coach_hint, get_review, load_knowledge,
                   next_level, parse_generation, retrieve, validate_suggestion)
from eval_harness import (check_determinism, evaluate_analysis, evaluate_difficulty,
                          evaluate_level_gating, evaluate_retrieval)
from logic_utils import DIFFICULTY_SETTINGS


def no_model(prompt):
    raise RuntimeError("model disabled")


# --- game state --------------------------------------------------------
def test_state_narrows_range_from_history():
    state = compute_state([50, 30], 41, 1, 100, 8)
    assert (state["cur_low"], state["cur_high"]) == (31, 49)
    assert state["attempts_left"] == 6


def test_state_ignores_invalid_raw_input():
    # the app logs invalid input as strings in history
    state = compute_state(["abc", "150", 50], 41, 1, 100, 8)
    assert state["guesses"] == [50]
    assert state["attempts_left"] == 7


# --- secret never reaches the model ------------------------------------
def test_prompt_never_contains_secret_range_info_only():
    seen = []
    get_coach_hint([50], 37, 1, 100, 8, generator=lambda p: seen.append(p) or "x\nGUESS: 25")
    assert "37" not in seen[0]  # secret is 37; only derived range 1-49 is shared
    assert "Remaining possible range: 1 to 49" in seen[0]


# --- guardrails ----------------------------------------------------------
@pytest.mark.parametrize("raw", [
    "Try 500.\nGUESS: 500",       # outside the full range
    "Try 60.\nGUESS: 60",         # outside the *remaining* range
    "No number given",            # no GUESS line
    "Repeat.\nGUESS: 50",         # repeats an earlier guess
])
def test_bad_model_output_falls_back(raw):
    hint = get_coach_hint([50], 30, 1, 100, 8, generator=lambda p: raw)
    assert hint["used_llm"] is False
    assert 1 <= hint["suggested"] <= 49 and hint["suggested"] != 50


def test_good_model_output_is_used():
    hint = get_coach_hint([50], 30, 1, 100, 8, generator=lambda p: "Go lower.\nGUESS: 25")
    assert hint["used_llm"] is True
    assert hint["suggested"] == 25


def test_model_exception_falls_back_and_reports_reason():
    hint = get_coach_hint([], 30, 1, 100, 8, generator=no_model)
    assert hint["used_llm"] is False
    assert "model disabled" in hint["reason"]


def test_parse_generation():
    assert parse_generation("Nice.\nGUESS: 12") == ("Nice.", 12)
    assert parse_generation("no guess here") == ("no guess here", None)


def test_validate_single_number_left_may_repeat():
    state = compute_state([5, 7], 6, 1, 10, 6)  # range collapsed to 6
    ok, _ = validate_suggestion("It is 6.", 6, state)
    assert ok


# --- retrieval -----------------------------------------------------------
def test_knowledge_rules_come_from_game_settings():
    docs = {d["id"]: d["text"] for d in load_knowledge()}
    for name, s in DIFFICULTY_SETTINGS.items():
        assert f"{name} uses the range {s['low']} to {s['high']} with {s['attempts']} attempts" in docs["difficulty_levels"]


def test_retrieval_returns_relevant_note():
    docs = load_knowledge()
    top = retrieve("first guess opening", docs, k=1)[0]["id"]
    assert top == "first_guess"


def test_retrieval_hit_rate_on_labelled_cases():
    score, rows = evaluate_retrieval()
    assert score == 1.0, rows


# --- reliability (full simulation, no network) ---------------------------
@pytest.mark.parametrize("name", list(DIFFICULTY_SETTINGS))
def test_coach_wins_every_game_with_valid_suggestions(name):
    result = evaluate_difficulty(name)
    assert result["win_rate"] == 1.0
    assert result["invalid_suggestions"] == 0


def test_hints_are_deterministic():
    assert check_determinism() == 0


# --- hint ladder / level gating -------------------------------------------
def test_next_level_climbs_and_caps():
    assert [next_level(n) for n in range(5)] == [1, 2, 3, 3, 3]


def test_level_costs_are_nudge_free_then_increasing():
    costs = [HINT_LEVELS[n]["cost"] for n in (1, 2, 3)]
    assert costs[0] == 0 and costs[0] < costs[1] < costs[2]


def test_low_level_notes_contain_no_numbers():
    # levels 1-2 may only quote the numbers the validator allows, so their notes must have none
    for doc in load_knowledge():
        if NOTE_LEVELS.get(doc["id"], 3) <= 2:
            assert not re.search(r"\d", doc["text"]), doc["id"]


def test_retrieval_respects_level():
    docs = load_knowledge()
    level1_ids = {d["id"] for d in docs_for_level(docs, 1)}
    assert "first_guess" not in level1_ids and "scoring" not in level1_ids
    hint = get_coach_hint([], 40, 1, 100, 8, level=1, generator=no_model)
    assert set(hint["sources"]) <= level1_ids


def test_unknown_notes_default_to_most_restricted_level():
    docs = [{"id": "brand_new_note", "text": "anything"}]
    assert docs_for_level(docs, 2) == []
    assert docs_for_level(docs, 3) == docs


def test_prompt_levels_reveal_only_their_facts():
    seen = {}
    for level in (1, 2, 3):
        get_coach_hint([50], 30, 1, 100, 8, level=level,
                       generator=lambda p, lv=level: seen.setdefault(lv, p) and "x")
    assert "49" not in seen[1] and "Remaining possible range" not in seen[1]
    assert "Remaining possible range: 1 to 49" in seen[2]
    assert "Previous guesses" not in seen[2]
    assert "Previous guesses: [50]" in seen[3]


@pytest.mark.parametrize("level,raw", [
    (1, "Think about bounds, maybe 25."),             # nudge with a number
    (1, "Go lower.\nGUESS: 25"),                      # nudge suggesting a guess
    (2, "The range is 1 to 49.\nGUESS: 25"),          # range hint suggesting a guess
    (2, "The range is 1 to 60."),                      # wrong bound
    (2, "Somewhere lower."),                           # states no range
])
def test_overshared_or_wrong_low_level_answers_fall_back(level, raw):
    hint = get_coach_hint([50], 30, 1, 100, 8, level=level, generator=lambda p: raw)
    assert hint["used_llm"] is False


def test_level_answers_that_obey_rules_are_accepted():
    assert get_coach_hint([50], 30, 1, 100, 8, level=1,
                          generator=lambda p: "What did your last answer rule out?")["used_llm"]
    assert get_coach_hint([50], 30, 1, 100, 8, level=2,
                          generator=lambda p: "It is between 1 and 49.")["used_llm"]


def test_level_three_rejects_numbers_it_was_not_given():
    hint = get_coach_hint([50], 30, 1, 100, 8, level=3,
                          generator=lambda p: "The secret is 30 probably.\nGUESS: 25")
    assert hint["used_llm"] is False


def test_level_gating_harness_has_no_violations():
    result = evaluate_level_gating()
    assert result["violations"] == 0
    assert result["overshare_blocked"] == result["overshare_tried"] > 0


# --- review -----------------------------------------------------------------
def test_analyze_flags_each_kind_of_mistake():
    kinds = [f["kind"] for f in analyze_guesses([50, 90, 90, 40], 30, 1, 100)]
    assert kinds == ["outside_range", "repeat", "off_middle"]


def test_analyze_ignores_invalid_input_and_perfect_play():
    assert analyze_guesses(["abc", 50, "999", 25], 30, 1, 100) == []


def test_mistake_detection_harness():
    result = evaluate_analysis()
    assert result["false_positives"] == 0
    assert result["detected"] == result["injected"] > 0
    assert result["unexpected_findings"] == 0


def test_review_fallback_is_deterministic_and_mentions_findings():
    review = get_review([50, 90], 30, 1, 100, 8, generator=no_model)
    assert review["used_llm"] is False
    assert "Guess #2 (90)" in review["text"]


def test_review_with_no_guesses_does_not_call_the_model():
    calls = []
    review = get_review([], 30, 1, 100, 8, generator=lambda p: calls.append(p) or "x")
    assert calls == [] and "Make a guess first" in review["text"]


def test_review_never_leaks_secret_while_playing():
    review = get_review([50], 30, 1, 100, 8, status="playing",
                        generator=lambda p: "You are close to 30!")
    assert review["used_llm"] is False and "30" not in review["text"]


def test_review_may_mention_secret_after_game_over():
    review = get_review([50, 30], 30, 1, 100, 8, status="won",
                        generator=lambda p: "You found 30 in 2 guesses.")
    assert review["used_llm"] is True


def test_review_prompt_contains_findings_but_not_secret():
    seen = []
    get_review([50, 90], 30, 1, 100, 8, generator=lambda p: seen.append(p) or "ok")
    assert "Guess #2 (90)" in seen[0] and "30" not in re.findall(r"\d+", seen[0])


# --- app integration ----------------------------------------------------------
def make_app(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    at = AppTest.from_file("app.py", default_timeout=10)
    at.run()
    at.session_state.secret = 42
    return at


def coach_btn(at):
    return next(b for b in at.button if b.label.startswith("Ask Coach"))


def review_btn(at):
    return next(b for b in at.button if b.label.startswith("Review"))


def guess(at, value):
    at.text_input[0].set_value(str(value)).run()
    at.button[0].click().run()


def test_coach_button_label_shows_next_level_and_cost(monkeypatch):
    at = make_app(monkeypatch)
    assert "Hint 1/3: Nudge (free)" in coach_btn(at).label
    coach_btn(at).click().run()
    assert "Hint 2/3: Range (-5 pts)" in coach_btn(at).label
    coach_btn(at).click().run()
    assert "Hint 3/3: Best move (-15 pts)" in coach_btn(at).label


def test_ladder_gives_nudge_then_range_then_move_and_charges_points(monkeypatch):
    at = make_app(monkeypatch)
    coach_btn(at).click().run()
    assert at.session_state.score == 0  # nudge is free
    coach_btn(at).click().run()
    assert at.session_state.score == -5
    coach_btn(at).click().run()
    assert at.session_state.score == -20
    assert not at.exception
    text = " ".join(i.value for i in at.info)
    assert "Nudge" in text and "Range" in text and "Suggested guess: 50" in text
    assert at.session_state.attempts == 0  # asking costs points, never attempts


def test_coach_button_disabled_after_three_hints(monkeypatch):
    at = make_app(monkeypatch)
    for _ in range(3):
        coach_btn(at).click().run()
    assert coach_btn(at).disabled
    assert "all 3 hints used" in coach_btn(at).label


def test_valid_guess_resets_the_ladder_and_label(monkeypatch):
    at = make_app(monkeypatch)
    for _ in range(3):
        coach_btn(at).click().run()
    guess(at, 50)
    assert at.session_state.coach_uses == 0
    assert not coach_btn(at).disabled
    assert "Hint 1/3: Nudge" in coach_btn(at).label
    assert not any("Suggested guess" in i.value for i in at.info)


def test_invalid_guess_does_not_reset_the_ladder(monkeypatch):
    at = make_app(monkeypatch)
    coach_btn(at).click().run()
    guess(at, "abc")
    assert at.session_state.coach_uses == 1


def test_review_button_disabled_until_a_guess_then_works(monkeypatch):
    at = make_app(monkeypatch)
    assert review_btn(at).disabled
    guess(at, 90)
    assert not review_btn(at).disabled
    guess(at, 90)  # repeat the same guess
    review_btn(at).click().run()
    assert not at.exception
    assert any("Review" in s.value and "repeated an earlier guess" in s.value for s in at.success)


def test_review_appears_automatically_when_game_ends(monkeypatch):
    at = make_app(monkeypatch)
    guess(at, 42)  # win on the first guess
    assert at.session_state.status == "won"
    assert any("Review" in s.value for s in at.success)
    assert coach_btn(at).disabled  # no hints once the game is over


def test_new_game_clears_coach_output(monkeypatch):
    at = make_app(monkeypatch)
    coach_btn(at).click().run()
    at.button[1].click().run()
    assert at.session_state.coach_uses == 0 and at.session_state.coach_log == []


def test_guidance_text_describes_every_level(monkeypatch):
    at = make_app(monkeypatch)
    caption = " ".join(c.value for c in at.caption)
    assert "Nudge (free)" in caption and "Range (-5 pts)" in caption
    assert "Best move (-15 pts)" in caption and "never sees the secret" in caption
