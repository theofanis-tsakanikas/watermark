#!/usr/bin/env python3
"""The Lake Formation access suite: for each principal, what is reachable and what is closed.

Both halves. A suite that only asserts what a principal *can* read passes just as happily on a
policy that grants everything — and "grants everything" is the shape a tag policy fails into,
because a broken tag expression selects more, not less.

The expectations are written here rather than derived from the policy. Deriving them would mean
asserting that the evaluator agrees with itself.
"""

from __future__ import annotations

import sys

from watermark.policy import load_policy

#: What each principal must be able to read, and what it must not. Written by hand, on purpose.
EXPECTED: dict[str, tuple[set[str], set[str]]] = {
    "role/watermark-settlement": (
        {
            "watermark_gold.settlement_hour",
            "watermark_gold.settlement_balancing_group",
            "watermark_silver.meter_interval",
            "watermark_bronze.quarantine",
        },
        # The control room's telemetry and the fraud investigation's outcomes. The second is the
        # one that matters: settlement and revenue protection both read personal data, and only
        # the purpose tag keeps them apart.
        {"watermark_gold.substation_telemetry", "watermark_gold.inspection_outcome"},
    ),
    "role/watermark-network-operations": (
        {"watermark_gold.substation_telemetry"},
        # Nothing personal at all. A curtailment decision is about a substation, and the
        # operator does not need to know whose house is behind it.
        {
            "watermark_gold.settlement_hour",
            "watermark_silver.meter_interval",
            "watermark_bronze.quarantine",
            "watermark_gold.inspection_outcome",
            "watermark_gold.settlement_balancing_group",
        },
    ),
    "role/watermark-revenue-protection": (
        {"watermark_gold.inspection_outcome"},
        # Not consumption. The purpose tag is what stops a fraud investigation becoming a
        # general licence to read what a household did last Tuesday.
        {
            "watermark_gold.settlement_hour",
            "watermark_silver.meter_interval",
            "watermark_bronze.quarantine",
            "watermark_gold.substation_telemetry",
            "watermark_gold.settlement_balancing_group",
        },
    ),
    "role/watermark-analyst": (
        {"watermark_gold.settlement_balancing_group", "watermark_gold.substation_telemetry"},
        {
            "watermark_gold.settlement_hour",
            "watermark_silver.meter_interval",
            "watermark_bronze.quarantine",
            "watermark_gold.inspection_outcome",
        },
    ),
}


def main() -> int:
    policy = load_policy()
    problems = list(policy.problems())

    for principal, (must_read, must_not_read) in sorted(EXPECTED.items()):
        reachable = policy.reachable(principal)
        allowed, denied = set(reachable.allowed), set(reachable.denied)

        for resource in sorted(must_read - allowed):
            problems.append(f"{principal} cannot read {resource} and must be able to")
        for resource in sorted(must_not_read & allowed):
            problems.append(
                f"{principal} may read {resource} and must not. This is the direction a tag "
                "policy fails in: a broken expression selects more, not less."
            )
        for resource in sorted(must_not_read - denied - allowed):
            problems.append(f"{principal}: {resource} is neither allowed nor denied")

    declared = set(EXPECTED)
    actual = set(policy.principals())
    for principal in sorted(actual - declared):
        problems.append(
            f"{principal} has a grant and no expectation in this suite. A principal nobody "
            "wrote an expectation for is a principal whose access nobody has reviewed."
        )

    if problems:
        print("policy-access: the reachable set is not what was expected\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    total = sum(len(allowed) + len(denied) for allowed, denied in EXPECTED.values())
    print(
        f"policy-access: {len(EXPECTED)} principals, {total} principal-resource pairs — every "
        "reachable set exact and every closed path closed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
