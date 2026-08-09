"""`python -m evals.freshness` — claim 4, scored."""

from __future__ import annotations

from evals.freshness import CASES
from evals.scoring import score

if __name__ == "__main__":
    raise SystemExit(score("claim 4 · no decision on a stale feature", CASES))
