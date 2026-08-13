"""`python -m evals.cases` — every synthetic case the cast declares, observed in the day."""

from __future__ import annotations

from evals.cases import CASES
from evals.scoring import score

if __name__ == "__main__":
    raise SystemExit(score("the cast · every declared case is observed", CASES))
