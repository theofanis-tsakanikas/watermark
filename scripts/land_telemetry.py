#!/usr/bin/env python3
"""Land the substation telemetry the capture emitted into `gold.substation_telemetry`.

**The table existed and nothing wrote it.** It was declared in `infra/lakehouse/glue.tf` with its
columns and its partition key, catalogued, granted, and empty — for as long as this project has
had a lakehouse. Two feature contracts read from it. Neither had ever been served, so neither had
ever failed, and an empty Iceberg table answers `SELECT` with zero rows and no error at all.

That is the failure mode `check_feature_sources.py` now refuses, and this is the other half of
the fix: something that puts rows in it.

**Why Athena and not a Glue job.** The volume is a few hundred small JSON objects — one per
substation per five minutes for the length of a capture — and a Spark cluster to move them
would cost more in startup than the whole capture costs in KPUs. `seed_reference.py` sets the
precedent for exactly this reason and this file follows it.

**Why the headroom is stored and not derived.** `substation_headroom_15m` aggregates it with
`min`. A minimum over `limit_w - load_w` and a minimum over a `headroom_w` column are the same
number right up until somebody adds a filter or a join to one of the two paths, and claim 3
compares those two paths against each other with no tolerance. Storing it means both mechanisms
read one column that one writer computed once.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from watermark.core.time import Instant  # noqa: E402

TABLE = "substation_telemetry"
#: Athena's statement size ceiling is 262,144 bytes. Rows are short, but a long capture makes
#: many of them, and one oversized INSERT fails the whole landing rather than a batch of it.
ROWS_PER_INSERT = 500


def _wait(athena, execution: str) -> None:
    while True:
        detail = athena.get_query_execution(QueryExecutionId=execution)["QueryExecution"]
        state = detail["Status"]["State"]
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"{state}: {detail['Status'].get('StateChangeReason', '')}")
        time.sleep(2)


def _run(athena, workgroup: str, database: str, sql: str) -> None:
    execution = athena.start_query_execution(
        QueryString=sql,
        WorkGroup=workgroup,
        QueryExecutionContext={"Database": database},
    )["QueryExecutionId"]
    _wait(athena, execution)


def read_telemetry(client, bucket: str, since_millis: int) -> list[dict[str, object]]:
    """Every telemetry object written since a moment, as rows.

    Bounded by *write* time rather than by event time. The prefix holds every capture this
    estate has ever driven, and landing all of it every run would grow the table without bound
    and re-land rows the last run already wrote. An undecodable object is skipped rather than
    fatal — the landing prefix carries quarantined records too, and a run that refuses to land
    anything because one object is malformed is a run that reports nothing at all.
    """
    rows: list[dict[str, object]] = []
    pages = client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="telemetry/")
    for page in pages:
        for item in page.get("Contents", ()):
            if item["LastModified"].timestamp() * 1000 < since_millis:
                continue
            body = client.get_object(Bucket=bucket, Key=item["Key"])["Body"].read()
            try:
                record = json.loads(body)
            except json.JSONDecodeError:
                continue
            try:
                moment = Instant.from_iso(str(record["event_time"]))
                load = int(record["load_w"])
                limit = int(record["limit_w"])
            except (KeyError, ValueError):
                continue
            rows.append(
                {
                    "substation_id": str(record["substation_id"]),
                    "event_time": moment.to_iso()[:19].replace("T", " "),
                    # The ingestion instant is when the record was *written*, which is the only
                    # honest answer available here: the emitter does not stamp one, and reusing
                    # the event time would make every row appear to have arrived instantly and
                    # would silently satisfy every freshness budget in the system.
                    "ingest_time": item["LastModified"].isoformat()[:19].replace("T", " "),
                    "load_w": load,
                    "limit_w": limit,
                    "headroom_w": limit - load,
                    "event_day": moment.to_iso()[:10],
                }
            )
    return rows


def create_statement(database: str, warehouse: str) -> str:
    """The table, created by its writer — ADR-0008, and the reason this exists at all.

    It used to be an `aws_glue_catalog_table` with `table_type = "ICEBERG"` in its parameters.
    That produces a catalogue entry which looks like an Iceberg table and carries no metadata
    location, and Athena refuses to write to it in as many words: *"Detected Iceberg type table
    without metadata location. Setting table_type parameter in Glue metastore to create an
    Iceberg table is not supported."*

    `meter_interval` learnt this years of commits ago. This table did not, because the lesson
    needs a writer to teach it and this table had none.
    """
    return f"""
        CREATE TABLE IF NOT EXISTS {database}.{TABLE} (
            event_time    TIMESTAMP,
            ingest_time   TIMESTAMP,
            substation_id STRING,
            load_w        BIGINT,
            limit_w       BIGINT,
            headroom_w    BIGINT,
            event_day     STRING
        )
        PARTITIONED BY (event_day)
        LOCATION '{warehouse}/gold/{TABLE}'
        TBLPROPERTIES ('table_type' = 'ICEBERG', 'format' = 'parquet')
    """


def insert_statements(database: str, rows: list[dict[str, object]]) -> list[str]:
    """The INSERTs, batched. Pure, so the SQL is inspectable without an estate."""
    statements = []
    for start in range(0, len(rows), ROWS_PER_INSERT):
        batch = rows[start : start + ROWS_PER_INSERT]
        values = ",\n  ".join(
            "(TIMESTAMP '{event_time}', TIMESTAMP '{ingest_time}', '{substation_id}', "
            "{load_w}, {limit_w}, {headroom_w}, '{event_day}')".format(**row)
            for row in batch
        )
        statements.append(
            f"INSERT INTO {database}.{TABLE} "
            f"(event_time, ingest_time, substation_id, load_w, limit_w, headroom_w, event_day)\n"
            f"VALUES\n  {values}"
        )
    return statements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--database", default="watermark_gold")
    parser.add_argument("--workgroup", required=True)
    parser.add_argument(
        "--warehouse",
        default="",
        help="s3://…/warehouse. Required the first time, when the table does not exist yet.",
    )
    parser.add_argument(
        "--since",
        default="",
        help="RFC-3339. Only objects written at or after this are landed; the prefix holds "
        "every capture the estate has ever driven.",
    )
    arguments = parser.parse_args(argv)

    # Imported here, not at module scope: the suite, the claim gates and preflight all run on a
    # machine with no cloud extra, and `read_telemetry` and `insert_statements` are where every
    # defect this file could have lives. `parity_live.py` sets the precedent.
    import boto3  # noqa: PLC0415

    since = Instant.from_iso(arguments.since).epoch_millis if arguments.since else 0
    s3 = boto3.client("s3")
    rows = read_telemetry(s3, arguments.bucket, since)

    if not rows:
        print(
            "::error::no telemetry was written since the given moment, so nothing was landed. "
            "The two substation features read this table and would resolve to nothing.",
            file=sys.stderr,
        )
        return 1

    athena = boto3.client("athena")
    if arguments.warehouse:
        _run(
            athena,
            arguments.workgroup,
            arguments.database,
            create_statement(arguments.database, arguments.warehouse.rstrip("/")),
        )

    statements = insert_statements(arguments.database, rows)
    for statement in statements:
        _run(athena, arguments.workgroup, arguments.database, statement)

    substations = sorted({str(row["substation_id"]) for row in rows})
    print(
        f"landed {len(rows)} telemetry rows into {arguments.database}.{TABLE} "
        f"across {len(substations)} substations ({', '.join(substations)}), "
        f"in {len(statements)} statements"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
