"""`python -m evals.erasure` — claim 6, scored."""

from __future__ import annotations

from evals.erasure import CASES
from evals.scoring import score

if __name__ == "__main__":
    raise SystemExit(score("claim 6 · erasure is complete to a declared boundary", CASES))
