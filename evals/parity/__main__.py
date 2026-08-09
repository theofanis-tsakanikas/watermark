"""`python -m evals.parity` — claim 3, scored."""

from __future__ import annotations

from evals.parity import CASES
from evals.scoring import score

if __name__ == "__main__":
    raise SystemExit(score("claim 3 · train/serve parity, between two mechanisms", CASES))
