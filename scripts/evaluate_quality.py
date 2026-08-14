#!/usr/bin/env python3
"""Run the Glue Data Quality ruleset against the table it names, and report rule by rule.

The ruleset in `infra/governance/quality.tf` has been declared, applied and attached to
`silver.meter_interval` since the lakehouse layer existed. **It has never been evaluated.** A
ruleset that is never run is a document with a Terraform resource in front of it, and it fails
in the direction that reads as coverage: the resource exists, `terraform plan` is clean, and
nothing anywhere has ever checked a single row against a single rule.

Six rules, and each of them asserts something the stream core *guarantees* — deduplication
before publication, no negative totals, a revision that names what it replaced, a lineage id on
every row. That is deliberate: a failure means the pipeline is wrong, never that the week was
cold. So a failed rule here is a defect, and this script exits non-zero on one.

**Per rule, not per run.** Glue reports an overall `PASSED`/`FAILED` and a result per rule, and
the overall verdict alone would tell somebody that something is wrong without telling them
which invariant broke — and these six invariants have completely different meanings. `IsPrimaryKey`
failing is a customer billed twice. `Completeness "lineage_id"` failing is claim 2 losing its
identity. Printing "FAILED" and stopping would waste the most useful thing the run produces.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Glue's own polling ceiling for a small table. An evaluation over a few hundred rows settles in
#: a minute or two; anything past this is a run that is not going to finish inside a capture.
TIMEOUT_SECONDS = 900
POLL_SECONDS = 15


def evaluate(glue, ruleset: str, database: str, table: str, role: str) -> dict:
    """Start one evaluation run and wait for it. Returns Glue's own result document."""
    started = glue.start_data_quality_ruleset_evaluation_run(
        DataSource={"GlueTable": {"DatabaseName": database, "TableName": table}},
        Role=role,
        RulesetNames=[ruleset],
    )
    run_id = started["RunId"]

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        detail = glue.get_data_quality_ruleset_evaluation_run(RunId=run_id)
        status = detail["Status"]
        if status in ("SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT"):
            return detail
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"the evaluation did not settle within {TIMEOUT_SECONDS}s (run {run_id})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ruleset", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--role", required=True, help="the role Glue assumes to read the table")
    arguments = parser.parse_args(argv)

    import boto3  # noqa: PLC0415 — the suite and preflight run with no cloud extra

    glue = boto3.client("glue")
    detail = evaluate(glue, arguments.ruleset, arguments.database, arguments.table, arguments.role)

    if detail["Status"] != "SUCCEEDED":
        print(
            f"::error::the data quality evaluation ended {detail['Status']}: "
            f"{detail.get('ErrorString', 'no reason given')}",
            file=sys.stderr,
        )
        return 1

    results = []
    for result_id in detail.get("ResultIds", []):
        results.append(glue.get_data_quality_result(ResultId=result_id))

    rules = [rule for result in results for rule in result.get("RuleResults", [])]
    if not rules:
        print(
            "::error::the evaluation succeeded and produced no rule results. A ruleset that "
            "evaluated nothing is the state this script exists to end, not a pass.",
            file=sys.stderr,
        )
        return 1

    print(f"\n### `{arguments.database}.{arguments.table}` against `{arguments.ruleset}`\n")
    print("| rule | outcome | what Glue saw |")
    print("|---|---|---|")
    for rule in rules:
        outcome = rule.get("Result", "UNKNOWN")
        note = rule.get("EvaluationMessage") or rule.get("Description") or ""
        print(f"| `{rule.get('Name', '?')}` | **{outcome}** | {note[:180]} |")

    failed = [rule for rule in rules if rule.get("Result") != "PASS"]
    if failed:
        print()
        for rule in failed:
            print(
                f"::error::{rule.get('Name')}: {rule.get('EvaluationMessage', 'no message')}",
                file=sys.stderr,
            )
        print(
            "::error::These rules assert what the stream core guarantees. A failure is a defect "
            "in the pipeline, not an unusual week — that is why the ruleset holds only "
            "invariants a correct pipeline cannot violate.",
            file=sys.stderr,
        )
        return 1

    scores = [result.get("Score") for result in results if result.get("Score") is not None]
    summary = f", score {min(scores):.2f}" if scores else ""
    print(f"\nall {len(rules)} rules passed against the deployed table{summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
