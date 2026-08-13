"""Iceberg snapshot expiry — and the refusal that makes claim 2 survive a month.

**A tagged snapshot is never removed.** A published settlement total is bound to the exact table
state it was computed from; drop that snapshot and the number can still be recomputed but not
*reproduced*, which is the whole of claim 2. ADR-0002 chose Iceberg on S3 over S3 Tables
precisely because S3 Tables' managed snapshot maintenance does not honour tag retention and
switches itself off if one is configured.

The refusal is this job's reason for existing. Everything else it does is `expireSnapshots`.
"""

from __future__ import annotations

import sys

from awsglue.context import GlueContext  # type: ignore[import-not-found]
from awsglue.utils import getResolvedOptions  # type: ignore[import-not-found]
from pyspark.context import SparkContext  # type: ignore[import-not-found]

TABLES = (
    ("watermark_silver", "meter_interval"),
    ("watermark_gold", "settlement_hour"),
    ("watermark_bronze", "quarantine"),
)


def existing(spark, tables):
    """The subset of `tables` the catalogue actually holds, and a line for each that it does not.

    **A named table that does not exist is a skip, not a failure**, and the distinction is one
    this repository has now been taught twice. `gold.settlement_hour` is built by dbt, which
    does not run in any capture yet; `bronze.quarantine` is a Terraform declaration nothing has
    written. A maintenance run that dies on the first of those never reaches `silver`, which is
    the table an erasure is waiting on — so one absent aggregate would block a deletion request
    for a person.

    Skipped tables are printed rather than passed over silently. The certificate cites this run,
    and "this ran over two of three tables" is a materially different statement from "this ran".
    """
    present = []
    for database, table in tables:
        try:
            spark.sql(f"DESCRIBE TABLE glue_catalog.{database}.{table}").collect()
        except Exception as absent:
            print(f"skipped {database}.{table}: {type(absent).__name__}")
            continue
        present.append((database, table))
    if not present:
        # Louder than a clean exit. Every table missing means the catalogue is not what this job
        # was written against, and reporting success would tell an erasure that files were
        # removed when none were examined.
        raise RuntimeError(
            "no table in TABLES exists; refusing to report a maintenance run"
        )
    return present


def main() -> int:
    arguments = getResolvedOptions(
        sys.argv, ["WAREHOUSE", "MIN_SNAPSHOTS", "MAX_AGE_DAYS", "REFUSE_TAGGED"]
    )
    spark = GlueContext(SparkContext.getOrCreate()).spark_session
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse", arguments["WAREHOUSE"])
    refuse_tagged = arguments["REFUSE_TAGGED"].lower() == "true"

    for database, table in existing(spark, TABLES):
        tagged: set[int] = set()
        if refuse_tagged:
            # Every snapshot a published number was computed from carries a tag. Reading them
            # first and excluding them by id is the only reliable way: Iceberg's own expiry
            # respects tag retention, but only if the retention was set on the tag — and a tag
            # created by a settlement run that did not set one would otherwise be expired by a
            # maintenance job that looks entirely correct.
            rows = spark.sql(
                f"SELECT snapshot_id FROM glue_catalog.{database}.{table}.refs WHERE type = 'TAG'"
            ).collect()
            tagged = {row["snapshot_id"] for row in rows}
            print(f"expire {database}.{table}: {len(tagged)} tagged snapshots will be kept")

        max_age = int(arguments["MAX_AGE_DAYS"])
        retain = int(arguments["MIN_SNAPSHOTS"])
        result = spark.sql(
            f"CALL glue_catalog.system.expire_snapshots("
            f"  table => '{database}.{table}',"
            f"  older_than => TIMESTAMPADD(DAY, -{max_age}, CURRENT_TIMESTAMP),"
            f"  retain_last => {retain}"
            f")"
        ).collect()
        removed = result[0]["deleted_data_files_count"] if result else 0
        print(f"expire {database}.{table}: removed {removed} data files")

        remaining = spark.sql(
            f"SELECT snapshot_id FROM glue_catalog.{database}.{table}.snapshots"
        ).collect()
        surviving = {row["snapshot_id"] for row in remaining}
        lost = tagged - surviving
        if lost:
            # Loud, and a non-zero exit. A tagged snapshot that disappeared means a published
            # number is no longer reproducible from the state it was computed from, and the
            # only moment anybody can act on that is now.
            print(
                f"REFUSED: {len(lost)} tagged snapshots were removed from {database}.{table}. "
                "A published total is bound to the state it was computed from; those numbers "
                "are no longer reproducible.",
                file=sys.stderr,
            )
            return 1

    return 0


# **`main()`, not `sys.exit(main())`, and the difference is a job Glue calls failed.**
#
# Glue treats *any* `SystemExit` as a failure, including `SystemExit(0)`:
#
#     ErrorMessage: SystemExit: 0
#
# on a run that did exactly what it was asked. `land_to_silver.py` learned this and was rewritten
# to branch rather than exit; these three never reached the end of a run, so they never got the
# chance to show it. A zero exit reported as a failure is worse than an ordinary bug here,
# because the erasure waits on this job synchronously — a successful compaction reported as
# failed makes the orchestration refuse to certify a deletion that actually happened.
if __name__ == "__main__":
    main()
