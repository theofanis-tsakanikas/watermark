"""`python -m evals.watermark` — claim 1, scored."""

from __future__ import annotations

from evals.scoring import score
from evals.watermark import CASES

if __name__ == "__main__":
    raise SystemExit(score("claim 1 · no decision from a window that has not closed", CASES))
