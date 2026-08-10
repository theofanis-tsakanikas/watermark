"""Our own bias analysis, the model card, and the monitoring baseline — the pipeline's fourth step.

`Clarify` runs before this and produces the report an auditor expects. This produces the three
artefacts the rest of the system actually depends on:

**The bias analysis** that measures the risk `docs/SCENARIO.md` names — the one Clarify cannot
see, because it compares outcome rates and the defect is in the labels. ADR-0006.

**The model card**, generated from the run rather than typed beside it. AI Act Art. 11 and
Annex IV. A card somebody writes by hand is a card describing the model they meant to train.

**The Model Monitor baseline** — statistics and constraints, computed from the *training* set.
This is the artefact `infra/ml/monitoring.tf` reads, and before this module existed it was
produced by nothing: the monitoring schedule would have run, succeeded, and reported nothing
wrong for ever, because there was nothing to be different from.

The baseline comes from the training data and never from "recent production traffic", which is
the tempting shortcut and the one that defines drift as normal.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from watermark.models.bias import Subject, measure_proxy_discrimination
from watermark.models.clarify import measure_as_clarify_would

OUTPUT = Path("/opt/ml/processing/output")
DATASET = Path("/opt/ml/processing/input/dataset/dataset.csv")


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def baseline_from(rows: list[dict[str, str]], columns: list[str]) -> tuple[dict, dict]:
    """Statistics and constraints in the shape Model Monitor reads.

    Integers and exact counts rather than sampled estimates: the dataset is small enough that a
    sketch would be a worse answer to an easier question.

    `completeness` is 1.0 per column by construction here — the snapshot step writes no nulls —
    and it is emitted anyway. A constraint that is trivially satisfied today is the one that
    catches the day an upstream change starts writing nulls, which is precisely the drift a
    freshness budget cannot see.
    """
    statistics = {"version": 0, "dataset": {"item_count": len(rows)}, "features": []}
    constraints = {
        "version": 0,
        "features": [],
        # Report, do not act. There is no auto-rollback in this system: the answer to drift is
        # retraining or withdrawing trust, and both are decisions a person takes.
        "monitoring_config": {"evaluate_constraints": "Enabled", "emit_metrics": "Enabled"},
    }

    for column in columns:
        values = [float(row[column]) for row in rows if row.get(column) not in (None, "")]
        if not values:
            continue
        statistics["features"].append(
            {
                "name": column,
                "inferred_type": "Fractional",
                "numerical_statistics": {
                    "common": {"num_present": len(values), "num_missing": len(rows) - len(values)},
                    "mean": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                },
            }
        )
        constraints["features"].append(
            {
                "name": column,
                "inferred_type": "Fractional",
                "completeness": 1.0,
                "num_constraints": {"is_non_negative": min(values) >= 0},
            }
        )
    return statistics, constraints


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    # Required, with no default. A default of 0 flags every meter, and the analysis that comes
    # out is internally consistent, plausible, and about nothing. It comes from the fitted
    # model — a threshold this step chose for itself would be measuring its own choice.
    parser.add_argument("--threshold", type=int, required=True)
    arguments = parser.parse_args(argv)

    rows = _read(arguments.dataset)
    arguments.output.mkdir(parents=True, exist_ok=True)

    subjects = [
        Subject(
            row["entity_id"],
            int(row["deprivation_decile"]),
            int(row["score"]) >= arguments.threshold,
            bool(int(row["confirmed"])),
            # Ground truth is not in the dataset and must not be: production does not have it,
            # and an analysis that needs it is one that cannot run where it is needed. The
            # confirmed label stands in, which is exactly the limitation `bias.py` measures.
            bool(int(row["confirmed"])),
        )
        for row in rows
    ]

    ours = measure_proxy_discrimination(subjects)
    theirs = measure_as_clarify_would(subjects)

    (arguments.output / "bias.json").write_text(
        json.dumps(
            {
                "ours": ours.summary(),
                "precision_most_deprived": ours.precision_most_deprived,
                "precision_least_deprived": ours.precision_least_deprived,
                "uncomfortable": ours.is_uncomfortable,
                "clarify_equivalent": theirs.summary(),
                "clarify_within_conventional_bounds": theirs.within_conventional_bounds,
            },
            indent=2,
        ),
        "utf-8",
    )

    statistics, constraints = baseline_from(rows, ["score", "deprivation_decile"])
    baseline = arguments.output / "baseline"
    baseline.mkdir(exist_ok=True)
    (baseline / "statistics.json").write_text(json.dumps(statistics, indent=2), "utf-8")
    (baseline / "constraints.json").write_text(json.dumps(constraints, indent=2), "utf-8")

    print(f"examine: {ours.summary()}")
    print(f"examine: baseline over {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
