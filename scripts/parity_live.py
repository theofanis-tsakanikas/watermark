#!/usr/bin/env python3
"""Claim 3 and claim 4 against the deployed estate, rather than against a model of it.

`evals/parity/` proves both offline over two mechanisms that share a contract and nothing else.
What it cannot prove is that the *deployed* mechanisms are those two: that what Athena computes
over Iceberg and what `GetRecord` returns from the Feature Store agree, on the same entity, at
the same instant, to the watt-hour.

**The two sides are genuinely different, and that is the design (ADR-0004).**

*Offline* is the contract compiled to bitemporal `as_of` SQL by `watermark.features.offline` and
executed by Athena: set-oriented, recomputed whole, both time axes bound as parameters.

*Online* is `watermark.features.online`'s incremental materialiser: per-entity state, each
record seen once, eviction when a record falls out of the window, no recomputation — written
with `PutRecord` and read back with `GetRecord`.

Collapse them into a shared helper and claim 3 becomes a function compared with itself,
reporting green for ever. That is why the two halves below import from two different modules and
why neither computes the other's answer.

**No tolerance.** The contract declares `Integral` with a scale because the Feature Store has no
decimal type and a double compared against Iceberg's decimal differs in the last bits by
construction. A mismatch of one is a mismatch.

**Claim 4 rides on the same fetch.** A served value older than the feature's freshness budget is
one a decision must refuse; this reports how many were stale when they were read. The budget
comes from the contract, and a feature without one cannot load — which is what makes claim 4
mechanical rather than a matter of remembering.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Any

from watermark.contracts.loader import load
from watermark.core.time import Duration, Instant
from watermark.features.offline import as_of_sql
from watermark.features.online import OnlineMaterialiser

#: How long to wait for one Athena statement. Generous for these volumes; a query that has not
#: settled by then is a fault rather than a slow day, and this says so instead of reporting an
#: empty result as agreement.
QUERY_TIMEOUT = Duration.of_minutes(3)

#: How long to wait after writing before reading back. The online store is eventually consistent
#: on write, and a read that raced the put would be reported as a divergence — the wrong finding
#: entirely, and the kind that sends somebody looking at the aggregator for a week.
SETTLE = Duration.of_seconds(20)


@dataclass(frozen=True, slots=True)
class Estate:
    """Where the two mechanisms live. Passed around rather than re-derived per call."""

    athena: Any
    runtime: Any
    contract: Any
    table: str
    workgroup: str
    feature_group: str


def _query(estate: Estate, sql: str, parameters: list[str] | None = None) -> list[list[str]]:
    """Run one statement and return its data rows, without the header.

    Parameters are *bound*, never interpolated. `as_of_sql`'s own comment is explicit that a
    feature query built by concatenation is one somebody eventually builds from a
    customer-supplied meter id, and this is the executor that would have made that true.
    """
    request: dict[str, Any] = {"QueryString": sql, "WorkGroup": estate.workgroup}
    if parameters:
        request["ExecutionParameters"] = parameters
    execution = estate.athena.start_query_execution(**request)["QueryExecutionId"]

    deadline = time.monotonic() + QUERY_TIMEOUT.millis / 1000
    while time.monotonic() < deadline:
        status = estate.athena.get_query_execution(QueryExecutionId=execution)["QueryExecution"][
            "Status"
        ]
        state = status["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"{state}: {status.get('StateChangeReason', '')}\n{sql}")
        time.sleep(2)
    else:
        raise RuntimeError(f"the query did not settle within {QUERY_TIMEOUT}\n{sql}")

    rows: list[list[str]] = []
    header_seen = False
    pages = estate.athena.get_paginator("get_query_results").paginate(QueryExecutionId=execution)
    for page in pages:
        for row in page["ResultSet"]["Rows"]:
            if not header_seen:
                header_seen = True
                continue
            rows.append([cell.get("VarCharValue", "") for cell in row["Data"]])
    return rows


def _instant(rendered: str) -> Instant:
    """Athena renders a timestamp as `2026-08-12 09:15:00.000`; the core reads ISO-8601."""
    return Instant.from_iso(rendered.strip().replace(" ", "T").split(".")[0] + "Z")


def _materialise(estate: Estate, limit: int):
    """The online mechanism: incremental, per entity, each record seen once.

    Fed in event-time order, because an incremental aggregator is order-dependent in exactly the
    way a query is not. Claim 2 is about the *stream core* not depending on arrival order; this
    is a different component and the ordering is part of its definition.
    """
    contract = estate.contract
    raw = _query(
        estate,
        f"SELECT {contract.entity_key}, {contract.event_time_column}, "
        f"{contract.source_column}, {contract.ingest_time_column} FROM {estate.table} "
        f"ORDER BY {contract.event_time_column}",
    )
    if not raw:
        raise RuntimeError("the silver table is empty; there is nothing to compare")

    materialiser = OnlineMaterialiser(contract)
    for entity_id, event_time, value, ingest in raw:
        materialiser.observe(entity_id, _instant(event_time), int(value), _instant(ingest))

    # **Two instants, because the query has two axes.** The event-time bound is the latest
    # reading the aggregator saw; the ingestion bound is the latest moment anything was known.
    #
    # Binding one instant to both was the first thing this harness got wrong, and the failure is
    # worth keeping in view: the day is *shifted* so that it ends when the capture begins, and
    # every record is ingested during the capture — so `first_seen_at <= max(event_time)` is
    # false for every row and the offline side returned nothing at all. "What did we know at the
    # start of the run" is a correct answer to a question nobody asked.
    as_of_event = _instant(max(row[1] for row in raw))
    as_of_ingest = _instant(max(row[3] for row in raw))
    entities = sorted({row[0] for row in raw})[:limit]

    written = 0
    for entity_id in entities:
        served = materialiser.serve(entity_id)
        if served is None:
            continue
        estate.runtime.put_record(
            FeatureGroupName=estate.feature_group,
            Record=[
                {"FeatureName": contract.entity_key, "ValueAsString": entity_id},
                {
                    "FeatureName": "event_time",
                    "ValueAsString": served.to_feature_store_event_time(),
                },
                {"FeatureName": contract.source_column, "ValueAsString": str(served.value)},
            ],
        )
        written += 1

    print(f"materialised {written} of {len(entities)} entities into {estate.feature_group}")
    return as_of_event, as_of_ingest, entities, written


def _compare(estate: Estate, as_of_ingest: Instant, entities: list[str]):
    """The offline mechanism, and the comparison. Set-oriented, recomputed whole, by Athena."""
    contract = estate.contract
    budget = Duration.of_seconds(contract.freshness_budget_seconds)
    offline_sql = as_of_sql(contract).replace(
        f"FROM {contract.source_table}", f"FROM {estate.table}"
    )
    ingest_bound = as_of_ingest.to_iso()[:19].replace("T", " ")

    agreed: list[str] = []
    diverged: list[str] = []
    missing: list[str] = []
    stale = 0

    for entity_id in entities:
        # **The online record is read first, because it decides which instant to ask about.**
        #
        # Claim 3 is that the served value equals the offline value *for the same entity at the
        # same instant*. An online record's window ends at that entity's own latest reading, and
        # meters do not all report at the same moment — so asking the offline store about the
        # globally latest instant asks about a window some of that meter's readings have already
        # fallen out of. It produced a consistent, plausible and entirely spurious
        # disagreement: online larger than offline on nineteen entities out of twenty.
        response = estate.runtime.get_record(
            FeatureGroupName=estate.feature_group,
            RecordIdentifierValueAsString=entity_id,
            FeatureNames=[contract.source_column, "event_time"],
        )
        record = {item["FeatureName"]: item["ValueAsString"] for item in response.get("Record", [])}

        if contract.source_column not in record:
            missing.append(entity_id)
            continue
        online = int(record[contract.source_column])
        served_at = Instant.from_iso(record["event_time"])
        event_bound = served_at.to_iso()[:19].replace("T", " ")

        # The placeholders in order: the entity, the window's lower bound, its upper bound, and
        # the ingestion bound. The first three are the event axis, pinned to what this record
        # claims; the last is the other axis and is a different instant.
        rows = _query(estate, offline_sql, [entity_id, event_bound, event_bound, ingest_bound])
        expected = rows[0][0] if rows and rows[0][0] else None

        if expected is None or int(float(expected)) != online:
            diverged.append(f"{entity_id}: offline {expected}, online {online}")
            continue
        agreed.append(entity_id)
        # Freshness is measured from the latest moment anything was known, not from the record's
        # own event time — which is zero by construction and would make claim 4 a tautology.
        if as_of_ingest.since(served_at).millis > budget.millis:
            stale += 1

    print(f"claim 3: {len(agreed)} agreed, {len(diverged)} diverged, {len(missing)} missing")
    print(f"claim 4: {stale} of {len(agreed)} served values were past the {budget} budget")
    return agreed, diverged, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--feature-group", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--workgroup", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--entities",
        type=int,
        default=20,
        help="How many entities to compare. Each is a separate as-of query and a separate "
        "GetRecord, so this is a real cost rather than a sample size to inflate.",
    )
    arguments = parser.parse_args(argv)

    # Imported here, not at module scope: the suite, the claim gates and preflight all run on a
    # machine with no cloud extra. `data/publish.py` sets the precedent.
    import boto3  # noqa: PLC0415

    contract = load().features[arguments.feature]
    estate = Estate(
        athena=boto3.client("athena", region_name=arguments.region),
        runtime=boto3.client("sagemaker-featurestore-runtime", region_name=arguments.region),
        contract=contract,
        table=f"{arguments.database}.{contract.source_table}",
        workgroup=arguments.workgroup,
        feature_group=arguments.feature_group,
    )

    _, as_of_ingest, entities, written = _materialise(estate, arguments.entities)
    if not written:
        print("::error::nothing was materialised, so nothing can be compared", file=sys.stderr)
        return 1

    time.sleep(SETTLE.millis / 1000)
    agreed, diverged, missing = _compare(estate, as_of_ingest, entities)

    problems = 0
    if diverged:
        print("::error::claim 3: the two mechanisms disagree", file=sys.stderr)
        for line in diverged[:10]:
            print(f"  {line}", file=sys.stderr)
        problems += 1
    if missing:
        # Kept apart from a divergence on purpose. "The online store has no value" and "it has
        # the wrong one" are different failures with different causes, and a harness that
        # counted them together would let a materialisation that wrote nothing look like one
        # that wrote correctly.
        print(
            f"::error::claim 3: {len(missing)} entities absent online, e.g. {missing[:5]}",
            file=sys.stderr,
        )
        problems += 1
    if not agreed:
        print(
            "::error::nothing was compared; a harness that compares nothing passes",
            file=sys.stderr,
        )
        problems += 1

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
