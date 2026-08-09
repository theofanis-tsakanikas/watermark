#!/usr/bin/env python3
"""The streaming adapter carries no semantic literal. ADR-0003, enforced.

Reasoning in `src/watermark/gates/adapter_thinness.py`. This is the runner, and the target of
the gate-proof mutation that plants `Time.minutes(15)` in the job.
"""

from __future__ import annotations

from pathlib import Path

from watermark.gates.adapter_thinness import main

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    raise SystemExit(main(ROOT / "streaming", ROOT))
