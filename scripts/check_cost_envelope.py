#!/usr/bin/env python3
"""A full capture must come in under the €100 the design is constrained by.

`CLAUDE.md` says a design that pushes past it is wrong before the budget is. That sentence is
only load-bearing if something checks it, so this does — against the rate card in
`src/watermark/observability/cost.py`, with the shapes the estate is actually configured for.

It is an estimate. Nothing has been billed and nothing will be; `docs/DECISIONS.md` 15 puts the
estate permanently out of scope for application. What this rules out is a *design* that could
not come in under the target, which is a question a rate card can answer.
"""

from __future__ import annotations

import sys

from watermark.observability.cost import CostModel, estimate

#: The capture shape: the Flink parallelism and shard count from `infra/streaming`, the online
#: store on, one endpoint, for one hour.
CAPTURE = CostModel(flink_kpus=2, kinesis_shards=12, feature_groups_online=2, endpoints=1)
CAPTURE_HOURS = 1

#: The ceiling, in euro cents.
ENVELOPE_CENTS = 10_000


def main() -> int:
    report = estimate(CAPTURE, decisions=250_000 * 4, meters=250_000, hours=CAPTURE_HOURS)
    print(report.summary())

    if report.total_cents > ENVELOPE_CENTS:
        print(
            f"\ncost-envelope: €{report.total_cents / 100:.2f} exceeds the €"
            f"{ENVELOPE_CENTS / 100:.0f} the design is constrained by. The design is wrong "
            "before the budget is — reduce the shape, not the target.",
            file=sys.stderr,
        )
        return 1

    headroom = ENVELOPE_CENTS - report.total_cents
    print(
        f"cost-envelope: within the €{ENVELOPE_CENTS / 100:.0f} target, with €"
        f"{headroom / 100:.2f} of headroom"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
