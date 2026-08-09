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


def main() -> int:
    arguments = getResolvedOptions(
        sys.argv, ["WAREHOUSE", "MIN_SNAPSHOTS", "MAX_AGE_DAYS", "REFUSE_TAGGED"]
    )
    spark = GlueContext(SparkContext.getOrCreate()).spark_session
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse", arguments["WAREHOUSE"])
    refuse_tagged = arguments["REFUSE_TAGGED"].lower() == "true"

    for database, table in TABLES:
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


if __name__ == "__main__":
    sys.exit(main())
