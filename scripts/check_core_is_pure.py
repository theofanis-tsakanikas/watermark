#!/usr/bin/env python3
"""The stream core imports no framework and reads no ambient state.

The rule and the reasoning live in `src/watermark/gates/core_purity.py`. This is the runner:
`make core-pure`, a CI step, a preflight check, and the target of two mutations in
`scripts/gate_proof.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from watermark.gates.core_purity import report, scan

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "watermark" / "core"


def main() -> int:
    findings = scan(CORE, ROOT)
    print(report(findings), file=sys.stderr if findings else sys.stdout)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
