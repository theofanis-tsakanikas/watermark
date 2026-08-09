"""`python -m evals.promotion` — claim 5, scored."""

from __future__ import annotations

from evals.promotion import CASES
from evals.scoring import score

if __name__ == "__main__":
    raise SystemExit(score("claim 5 · no model reaches an endpoint ungated", CASES))
