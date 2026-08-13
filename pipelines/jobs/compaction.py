"""Iceberg compaction, run as a Glue job.

ADR-0002 chose Iceberg on S3 over S3 Tables knowing that this file would have to exist. It is
the price the decision accepted, and the two things that price bought are visible here: the job
is **invocable on demand** — the erasure Step Function calls it synchronously and waits — and it
**reports which files it rewrote**, which is what turns claim 6's certificate from a promise
into a statement.

250,000 meters uploading in a burst after each interval boundary is a small-file generator. Left
alone, the settlement queries get slower every hour and nothing reports it.
"""

from __future__ import annotations

import sys

from awsglue.context import GlueContext  # type: ignore[import-not-found]
from awsglue.utils import getResolvedOptions  # type: ignore[import-not-found]
from pyspark.context import SparkContext  # type: ignore[import-not-found]

#: Tables compacted on every run. Named rather than discovered: a job that compacts whatever it
#: finds is a job that starts rewriting a table somebody added for a different purpose.
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
        raise SystemExit("no table in TABLES exists; refusing to report a maintenance run")
    return present


def main() -> int:
    arguments = getResolvedOptions(sys.argv, ["WAREHOUSE", "TARGET_FILE_SIZE_MB"])
    target_bytes = int(arguments["TARGET_FILE_SIZE_MB"]) * 1024 * 1024

    spark = GlueContext(SparkContext.getOrCreate()).spark_session
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse", arguments["WAREHOUSE"])

    for database, table in existing(spark, TABLES):
        target = f"glue_catalog.{database}.{table}"
        # `rewrite_data_files` with an explicit target size. Without the size it uses Iceberg's
        # default, which is not the number `infra/lakehouse` configured — and a compaction that
        # disagrees with its own configuration is one nobody can reason about from the plan.
        result = spark.sql(
            f"CALL glue_catalog.system.rewrite_data_files("
            f"  table => '{database}.{table}',"
            f"  options => map('target-file-size-bytes','{target_bytes}')"
            f")"
        ).collect()
        rewritten = result[0]["rewritten_data_files_count"] if result else 0
        added = result[0]["added_data_files_count"] if result else 0
        # Printed, not logged at debug. The erasure certificate cites this run, so the number
        # of files it rewrote has to be findable afterwards without re-running anything.
        print(f"compaction {target}: rewrote {rewritten} files into {added}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
