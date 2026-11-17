# Binary search strategy
Always guess the middle of the remaining range. Each guess then removes half of the remaining numbers, so you find the secret in the fewest possible guesses. If the feedback was "Too High", the secret is below your guess; if "Too Low", it is above.
