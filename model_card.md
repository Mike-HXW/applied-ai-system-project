# Model Card: Number Guesser with a RAG Strategy Coach

**System:** a Streamlit number-guessing game with an AI coach. The coach retrieves strategy notes, asks Gemini to write a hint or a review of the player's guesses, and checks the answer with a validator before showing it. See the [README](README.md) for the architecture.

## Limitations and Biases

- **The game is already solved.** The best guess is always the middle of the remaining range, so the coach has little real strategy to teach. I made it more useful with a review of the player's own guesses and a three-step hint ladder, but the advice is still simple.
- **The coach assumes binary search is "good play."** The review flags a guess as "far from the middle" if it removes far fewer possibilities than a middle guess (the cutoff, 1.5 times the ideal, is my own judgment). A player who takes quick lucky guesses or likes risk is marked as making mistakes even though the game allows it. This is a bias built into what I chose to reward.
- **Small, hand-written knowledge base.** About a dozen short English notes, searched by keyword (BM25), so paraphrased situations can be missed. My first version retrieved the wrong note for mid-game states.
- **Only three kinds of mistake** (repeated guess, guess already ruled out, guess far from the middle) and only English output.
- **Limited evidence for the live model.** The live check was 10 hints, all of which passed the validator. That shows the prompts work, but it is too small to show the model never breaks the rules. The harness also tests mistakes I defined myself, so it checks consistency, not whether a human agrees these are mistakes.
- **The secret is not fully protected.** The coach never sees it, but the "Developer Debug Info" panel still shows it to the player. I left it on for development.
- **Hint costs are untuned.** Nudge is free, Range costs 5 points and Best move costs 15, and the score can go negative.
- **Dependence on a third-party service.** If the API is down, rate limited or the key is missing, the app falls back to plainer, repetitive built-in hints. Only game numbers (the range and previous guesses) are sent to Google, with no personal data.

## Misuse and Prevention

The risk is low, because the app only takes numbers and the model only sees game facts. Possible misuse and how it is handled:

- **Cheating with the coach.** A player could ask for the best move every turn. Hints are tiered, each level reveals more, and levels 2 and 3 cost points, so the game rewards players who think first. The validator blocks any answer that shares more than its level allows.
- **Leaking the secret.** The secret is never put in a prompt, so the model cannot reveal it. The review may mention it only after the game is over.
- **Prompt injection.** No free text from the player reaches the model: guesses must be whole numbers, and invalid input is dropped before the prompt is built. If this code were reused with free-text input, it would need injection defenses first.
- **Key and cost abuse.** The API key is read from an environment variable and never stored in a file or committed. Outputs are short, calls time out after 15 seconds, and a spending limit can be set in the Gemini console.

## What Surprised Me While Testing

- **Fixing one retrieval case broke another.** The harness scored retrieval at 80%. Fixing the missed "narrowing the range" note made a different case ("small range") fail, until I weighted the situation-specific query terms. It showed me why a labelled test set matters: I could not see this by trying a few hints by hand.
- **A UI bug that playing would not have found.** A UI test showed that the Ask Coach button stayed enabled on the guess that ended the game, because it was drawn before the guess was scored.
- **The live model passed every time.** In the 10 live hints, none needed the fallback. The guardrail's value showed up mainly in the simulated tests, where a fake over-sharing model was blocked 198 of 198 times. I also found that Google's Gemini docs had changed to a new API since I last knew it, so I had to check the current docs instead of relying on memory.

## Collaboration with AI

I worked with Claude Code (and an earlier chat with another assistant) to plan, build and test the project. The AI wrote most of the code and tests. I chose the direction and judged the results: I picked the features, supplied the Gemini key, noticed that the first coach was too simple ("always says use binary search"), and decided the wording of the game feedback.

- **A suggestion I accepted:** the plan to build a RAG strategy coach together with a reliability harness, which covers two of the required AI categories for little extra work. It also gave me a way to show the coach works with numbers (win rate, retrieval hit rate, level rule checks) instead of only claiming it. When the first coach turned out too repetitive, I accepted the AI's idea to coach the player's own guesses and to sell hints in steps with a point cost.
- **A suggestion I did not accept as written:**
  - The first plan assumed the Claude API. I changed it to Gemini because I already had a Gemini key and little budget. The AI then redesigned the model call around Gemini and checked Google's current docs instead of guessing the SDK.
  - The other assistant's pitch said the coach "fixes the problem that the debug panel gives away the secret." That is not true, because the panel still shows the secret, so I did not rely on that claim and listed it as a limitation above.
  - I also dropped the idea of an AI auto-player (another option from that chat), because it needs several model calls per game and would not add much for this project.
  - When I wondered if the "Too High / Too Low" message should be removed, the AI argued against it, since the Nudge and the review depend on the player seeing it. I accepted that and only changed the wording to a plain "Your guess is too high."
