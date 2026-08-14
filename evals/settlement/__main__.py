"""`python -m evals.settlement` — the settlement contract, and doctrine 4, scored."""

from __future__ import annotations

from evals.scoring import score
from evals.settlement import CASES

if __name__ == "__main__":
    raise SystemExit(score("the settlement path · doctrine 4 and its contract", CASES))
