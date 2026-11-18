import streamlit as st
from coach import HINT_LEVELS, MAX_LEVEL, get_coach_hint, get_review, next_level
from logic_utils import DEFAULT_DIFFICULTY, DIFFICULTY_SETTINGS, check_guess, get_attempt_limit, get_range_for_difficulty, new_game_state, parse_guess, update_score

st.set_page_config(page_title="Number Guesser", page_icon="🎮")

st.title("🎮 Guess the serect number")
st.caption("Write your guess and see what happen next")
#st.title("🎮 Game Glitch Investigator")
#st.caption("An AI-generated guessing game. Something is off.")

st.sidebar.header("Settings")

difficulty = st.sidebar.selectbox(
    "Difficulty",
    list(DIFFICULTY_SETTINGS),
    index=list(DIFFICULTY_SETTINGS).index(DEFAULT_DIFFICULTY),
)

attempt_limit = get_attempt_limit(difficulty) # FIX: single source of truth in logic_utils

low, high = get_range_for_difficulty(difficulty)

st.sidebar.caption(f"Range: {low} to {high}")
st.sidebar.caption(f"Attempts allowed: {attempt_limit}")
score_box = st.sidebar.empty()


def show_score():
    score_box.metric("Score", st.session_state.score)


def reset_coach():
    # Hints restart after every valid guess, New Game and difficulty change.
    st.session_state.coach_uses = 0
    st.session_state.coach_log = []
    st.session_state.coach_review = None


if st.session_state.get("difficulty") != difficulty:
    # FIX: first load or a difficulty change restarts the game completely
    for key, value in new_game_state(low, high).items():
        st.session_state[key] = value
    st.session_state.difficulty = difficulty
    reset_coach()

show_score()

st.subheader("Make a guess")

info_box = st.empty()


def show_info(): # FIX: additional module for data
    info_box.info(
        f"Guess a number between {low} and {high}. " # FIX: use the real range
        f"Attempts left: {attempt_limit - st.session_state.attempts}"
    )


with st.expander("Developer Debug Info"):
    debug_box = st.empty()


def show_debug(): # FIX: panel is redrawn after the guess so it never shows stale values
    with debug_box.container():
        st.write("Secret:", st.session_state.secret)
        st.write("Attempts:", st.session_state.attempts)
        st.write("Score:", st.session_state.score)
        st.write("Difficulty:", difficulty)
        st.write("History:", st.session_state.history)


show_debug()

raw_guess = st.text_input(
    "Enter your guess:",
    key=f"guess_input_{difficulty}_{st.session_state.get('input_reset', 0)}" # FIX: changing the key clears the box
)

col1, col2, col3 = st.columns(3)
with col1:
    submit = st.button("Submit Guess 🚀")
with col2:
    new_game = st.button("New Game 🔁")
with col3:
    show_hint = st.checkbox("Show hint", value=True)

if new_game:
    for key, value in new_game_state(low, high).items():
        st.session_state[key] = value
    st.session_state.input_reset = st.session_state.get("input_reset", 0) + 1 # FIX: clear the guess box on New Game
    reset_coach()
    st.success("New game started.")
    st.rerun()

def cost_text(cost):
    return "free" if cost == 0 else f"-{cost} pts"


# ---- Coach panel -----------------------------------------------------------
st.markdown("##### 🧠 Coach")
playing = st.session_state.status == "playing"
# A valid guess starts a fresh hint ladder. Reset BEFORE drawing the coach
# buttons so their labels are not stale for the guess being submitted now.
pending_ok, pending_guess, _ = parse_guess(raw_guess, low, high) if (playing and submit) else (False, None, None)
pending_valid_guess = pending_ok
if pending_valid_guess:
    reset_coach()
# Will this guess end the game? Then hints are already unavailable on this run.
game_ends_now = pending_valid_guess and (
    check_guess(pending_guess, st.session_state.secret) == "Win"
    or st.session_state.attempts + 1 >= attempt_limit
)
coach_enabled = playing and not game_ends_now
has_guesses = pending_valid_guess or any(isinstance(g, int) for g in st.session_state.history)
uses = st.session_state.coach_uses
level = next_level(uses)
hints_used_up = uses >= MAX_LEVEL

coach_col, review_col = st.columns(2)
with coach_col:
    if hints_used_up:
        coach_label = f"Ask Coach 🧠 · all {MAX_LEVEL} hints used"
    else:
        step = HINT_LEVELS[level]
        coach_label = f"Ask Coach 🧠 · Hint {level}/{MAX_LEVEL}: {step['name']} ({cost_text(step['cost'])})"
    ask_coach = st.button(coach_label, disabled=hints_used_up or not coach_enabled)
with review_col:
    review_clicked = st.button("Review my guesses 🔍 (free)", disabled=not has_guesses)

st.caption(
    "Hints go up one step each time you ask, and start over after your next guess: "
    + " → ".join(f"{h['name']} ({cost_text(h['cost'])})" for h in HINT_LEVELS.values())
    + ". The coach never sees the secret number."
)
with st.expander("What can the coach do?"):
    st.markdown(
        "**Ask Coach** gives a stronger hint each time. Points are taken from your score when you ask.\n\n"
        + "\n".join(f"{n}. **{h['name']}** ({cost_text(h['cost'])}): {h['gives']}"
                    for n, h in HINT_LEVELS.items())
        + "\n\n**Review my guesses** (free) points out repeated guesses, guesses that "
        "were already ruled out, and guesses far from the middle. It also appears "
        "automatically when the game ends."
    )
coach_area = st.container()  # filled at the end of the script so it is never stale


def render_coach_area():
    with coach_area:
        for hint in st.session_state.coach_log:
            step = HINT_LEVELS[hint["level"]]
            body = f"**Hint {hint['level']}/{MAX_LEVEL} · {step['name']}**: {hint['text']}"
            if hint["suggested"] is not None:
                body += f"  \n**Suggested guess: {hint['suggested']}**"
            st.info(body)
            source = "AI-written" if hint["used_llm"] else "built-in fallback"
            st.caption(f"Based on: {', '.join(hint['sources']) or 'no notes'} · {source}"
                       f" · cost: {cost_text(hint['cost'])}")
            if hint["reason"]:
                st.caption(f"AI unavailable: {hint['reason']}")
        game_over = st.session_state.status != "playing"
        if game_over and has_valid_guesses() and st.session_state.coach_review is None:
            with st.spinner("Coach is reviewing your game..."):
                st.session_state.coach_review = run_review()
        review = st.session_state.coach_review
        if review:
            st.success(f"🔍 **Review**: {review['text']}")
            source = "AI-written" if review["used_llm"] else "built-in fallback"
            st.caption(f"Based on: {', '.join(review['sources']) or 'no notes'} · {source}")
            if review["reason"]:
                st.caption(f"AI unavailable: {review['reason']}")


def has_valid_guesses():
    return any(isinstance(g, int) for g in st.session_state.history)


def run_review():
    return get_review(st.session_state.history, st.session_state.secret, low, high,
                      attempt_limit, status=st.session_state.status)


if ask_coach and playing:  # a stale click after game over must not charge points
    # The coach sees only the derived range, never the secret itself.
    with st.spinner("Coach is thinking..."):
        hint = get_coach_hint(st.session_state.history, st.session_state.secret,
                              low, high, attempt_limit, level=level)
    st.session_state.coach_log.append(hint)
    st.session_state.coach_uses += 1
    st.session_state.score -= hint["cost"]
    st.rerun()

if review_clicked:
    with st.spinner("Coach is reviewing your guesses..."):
        st.session_state.coach_review = run_review()
    st.rerun()

show_info()

if st.session_state.status != "playing":
    if st.session_state.status == "won":
        st.success("You already won. Start a new game to play again.")
    else:
        st.error("Game over. Start a new game to try again.")
    render_coach_area()
    show_score()
    st.stop()

if submit:
    ok, guess_int, err = parse_guess(raw_guess, low, high)

    if not ok:
        st.session_state.history.append(raw_guess)
        st.error(err)
        # FIX: invalid input is logged only; no attempt used, no score change
    else:
        st.session_state.attempts += 1 # FIX: only valid guesses use an attempt
        st.session_state.history.append(guess_int)

        outcome = check_guess(guess_int, st.session_state.secret)
        message = { #FIX: modify the hint message to be consistent to the truth
            "Win": "🎉 Correct!",
            # States the result plainly; the coach is where direction and strategy come from.
            "Too High": "📉 Your guess is too high.",
            "Too Low": "📈 Your guess is too low.",
        }[outcome]

        if show_hint:
            st.warning(message)

        st.session_state.score = update_score(
            current_score=st.session_state.score,
            outcome=outcome,
            attempt_number=st.session_state.attempts,
        )

        if outcome == "Win":
            st.balloons()
            st.session_state.status = "won"
            st.success(
                f"You won! The secret was {st.session_state.secret}. "
                f"Final score: {st.session_state.score}"
            )
        else:
            if st.session_state.attempts >= attempt_limit:
                st.session_state.status = "lost"
                st.session_state.score = 0 # FIX: a lost game scores 0
                st.error(
                    f"Out of attempts! "
                    f"The secret was {st.session_state.secret}. "
                    f"Score: {st.session_state.score}"
                )

show_info() # FIX: refresh "Attempts left" after the guess is processed
show_debug()
show_score()
render_coach_area()

st.divider()
st.caption("Built by an AI that claims this code is production-ready.")
