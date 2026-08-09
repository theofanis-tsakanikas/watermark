"""`python -m evals.replay` — claim 2, scored."""

from __future__ import annotations

from evals.replay import CASES
from evals.scoring import score

if __name__ == "__main__":
    raise SystemExit(score("claim 2 · replay is identical", CASES))
