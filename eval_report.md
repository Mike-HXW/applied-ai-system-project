# Coach reliability report

## Simulated games (every secret, coach followed)

| Difficulty | Games | Win rate | Avg guesses | Max guesses | Invalid suggestions |
|---|---|---|---|---|---|
| Easy | 20 | 100% | 3.70 | 5 | 0 |
| Normal | 100 | 100% | 5.80 | 7 | 0 |
| Hard | 50 | 100% | 4.86 | 6 | 0 |

## Determinism: 0 mismatches across repeated runs

## Retrieval relevance (expected note in top 3)

Hit rate: 100%

- PASS: expected `first_guess`, got ['first_guess', 'binary_search', 'scoring']
- PASS: expected `few_attempts_left`, got ['few_attempts_left', 'small_range', 'narrowing_range']
- PASS: expected `small_range`, got ['narrowing_range', 'binary_search', 'small_range']
- PASS: expected `avoid_repeats`, got ['avoid_repeats', 'narrowing_range', 'binary_search']
- PASS: expected `narrowing_range`, got ['narrowing_range', 'binary_search', 'learning_from_feedback']

## Mistake detection (review feature)

- Perfect binary-search players flagged: 0 of 170 games (target 0)
- Injected mistakes detected: 984 of 984
- Unexpected extra findings: 0 (target 0)
  - repeat: 328/328
  - outside_range: 328/328
  - off_middle: 328/328

## Hint-level gating

- Fallback hints that obey their level's rules: 297 of 297
- Over-sharing model answers blocked at levels 1-2: 198 of 198

## Live model sample

10/10 hints were AI-written and passed the guardrail; 0 fell back to the deterministic hint.
