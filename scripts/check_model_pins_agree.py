#!/usr/bin/env python3
"""The local model's pins and the pipeline's hyperparameters are the same experiment.

`src/watermark/models/gradient.py` fits the boosted forecaster on a laptop. `infra/ml/pipeline.tf`
fits it on SageMaker. ADR-0005 promises that the same snapshot, image and seed yield the same
metrics — and that promise is void the moment the two disagree about what the seed *is*.

The failure is silent in the worst way: both runs succeed, both report metrics, and the metrics
differ for a reason nobody looks for, because the obvious explanation is the data.

Nothing here reaches AWS.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "src" / "watermark" / "models" / "gradient.py"
PIPELINE = ROOT / "infra" / "ml" / "pipeline.tf"

#: `objective` differs on purpose and is excluded: the local model forecasts load (a regression)
#: and the pipeline fits the anomaly scorer (a classification). Everything below decides *how*
#: the fit proceeds rather than what it fits, and those must match.
COMPARED = ("seed", "nthread", "tree_method", "max_depth", "eta", "num_round")


def _local() -> dict[str, str]:
    tree = ast.parse(LOCAL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "HYPERPARAMETERS":
            return {
                key.value: str(value.value)
                for key, value in zip(node.value.keys, node.value.values, strict=True)
            }
    raise SystemExit("no HYPERPARAMETERS in gradient.py — the check's target has moved")


def _pipeline() -> dict[str, str]:
    text = PIPELINE.read_text(encoding="utf-8")
    block = re.search(r"HyperParameters = \{(.*?)\n          \}", text, re.S)
    if not block:
        raise SystemExit("no HyperParameters block in pipeline.tf — the check's target has moved")
    return dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', block.group(1)))


def main() -> int:
    local, remote = _local(), _pipeline()
    problems = []
    for key in COMPARED:
        if key not in local:
            problems.append(f"`{key}` is not pinned in gradient.py")
        elif key not in remote:
            problems.append(f"`{key}` is not pinned in pipeline.tf")
        elif local[key] != remote[key]:
            problems.append(
                f"`{key}`: gradient.py says {local[key]}, pipeline.tf says {remote[key]}"
            )

    if problems:
        print(
            "model-pins: the local fit and the pipeline fit are different experiments\n",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"model-pins: {len(COMPARED)} pins agree between gradient.py and pipeline.tf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
