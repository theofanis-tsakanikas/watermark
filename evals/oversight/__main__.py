"""`python -m evals.oversight` — claim 7, scored."""

from __future__ import annotations

from evals.oversight import CASES
from evals.scoring import score

if __name__ == "__main__":
    raise SystemExit(score("claim 7 · no automatic consequential decision about a person", CASES))
