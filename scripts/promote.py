#!/usr/bin/env python3
"""Evaluate the promotion gate against a pinned dataset, for a named human approver.

**This is the one step between a registered model and an endpoint**, and it exists because
`infra/ml/` cannot be the thing that decides. `promoted_model_name` has no default and the
endpoint cannot be enabled without it; what fills it is a promotion, and a promotion is a
judgement with a name on it.

It runs the *same* `PromotionGate` that `evals/promotion/` exercises twelve ways, over the same
`train_anomaly_scorer` and the same `measure_proxy_discrimination`. A second implementation here
would be a gate that agrees with the tested one until the day it does not, and the day it does
not is the day a model reaches production.

**The dataset is the pinned one**, downloaded from the pipeline's own output rather than
regenerated: the whole point of `PinTheSnapshot` is that the rows a model was fitted on can be
read again afterwards. Regenerating them here would evaluate the gate against a dataset nobody
trained on.

**The approver is a required argument with no default**, and `FORBIDDEN_APPROVERS` refuses the
pipeline's own identities. Doctrine 5: nothing approves itself.

Exit codes: 0 promoted, 1 refused with the reason on stderr, 2 the inputs were unusable. The
middle one is not an error in the operational sense — the gate refusing the model this
repository trained is the documented result of claim 5 — so the workflow that calls this says
so rather than printing a stack trace.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from watermark.core.time import Instant
from watermark.models.bias import Subject, measure_proxy_discrimination
from watermark.models.promotion import (
    THRESHOLDS,
    Approval,
    PromotionGate,
    PromotionRefused,
)
from watermark.models.train import Example, train_anomaly_scorer

#: What the model is for and what it can go wrong at. Both are required on the card by
#: `PromotionGate._card`, and both are properties of the decision rather than of the run — so
#: they are stated here, once, rather than passed in by whoever happens to be promoting.
INTENDED_PURPOSE = "Rank meters for inspection. Never actuated automatically (claim 7)."
HAZARD = "Proxy discrimination through the inspection feedback loop. See docs/BIAS-FINDING.md."


def _examples(rows: list[dict[str, str]], at: Instant) -> list[Example]:
    return [
        Example(row["entity_id"], at, (int(row["score"]),), int(row["confirmed"])) for row in rows
    ]


def _subjects(rows: list[dict[str, str]], threshold: int) -> list[Subject]:
    """The bias analysis, over the labels the model was actually fitted against.

    The fourth argument is ground truth, and here it is the same column as the third. That is
    not a shortcut: the pinned dataset carries one label column by design, because production
    has one. `docs/BIAS-FINDING.md` is precisely about what that costs, and a promotion that
    reached for a truth column production does not have would be measuring a system nobody runs.
    """
    return [
        Subject(
            row["entity_id"],
            int(row["deprivation_decile"]),
            int(row["score"]) >= threshold,
            bool(int(row["confirmed"])),
            bool(int(row["confirmed"])),
        )
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True, help="The pinned dataset.csv")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument(
        "--approver",
        required=True,
        help="The human who takes responsibility. No default; the pipeline's own identities "
        "are refused by name.",
    )
    parser.add_argument(
        "--parity-holds",
        choices=("true", "false"),
        required=True,
        help="Whether train/serve parity held for this run. Doctrine 7: the one door with no "
        "key — a mismatch cannot be waived by anybody, including the approver.",
    )
    arguments = parser.parse_args(argv)

    with arguments.dataset.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        print(f"promote: {arguments.dataset} has no rows", file=sys.stderr)
        return 2

    at = Instant.from_iso(arguments.as_of)
    run = train_anomaly_scorer(_examples(rows, at), arguments.snapshot, at)
    bias = measure_proxy_discrimination(_subjects(rows, run.model.threshold))
    card = run.model_card(intended_purpose=INTENDED_PURPOSE, hazard=HAZARD)

    try:
        PromotionGate(THRESHOLDS).evaluate(
            run=run,
            bias=bias,
            approval=Approval(approver=arguments.approver, at=at),
            model_card=card,
            parity_holds=arguments.parity_holds == "true",
        )
    except PromotionRefused as refusal:
        print(f"promote: REFUSED — {refusal.reason}", file=sys.stderr)
        print(f"  {refusal}", file=sys.stderr)
        return 1

    # Printed, because the endpoint's `promoted_model_name` has to be *this* artefact and a
    # promotion that does not name what it promoted is one nobody can audit afterwards.
    print(f"promote: PROMOTED by {arguments.approver}")
    print(f"  snapshot     {run.snapshot}")
    print(f"  data digest  {run.data_digest}")
    print(f"  artefact     {run.model.digest()}")
    print(f"  threshold    {run.model.threshold}")
    print(f"  metrics      {run.metrics}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
