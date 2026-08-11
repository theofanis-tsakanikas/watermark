"""Merge landed windows into the silver table.

The streaming job decides when a window closes and writes the result as a JSON line. This is
what makes it a row a settlement query can total, an erasure can delete and a correction can
restate — and it is a **MERGE**, which is the whole reason the lakehouse layer chose Iceberg
format-version 2 with merge-on-read.

**Why a Glue job rather than the Flink job itself.** Writing Iceberg from PyFlink means a
catalog factory resolved by the planner in the driver, a platform that loads exactly one jar,
and Iceberg constructing a Hadoop `Configuration` for a catalog that is Glue and an IO that is
S3. It was tried, four layers deep, and abandoned: teams that write Flink to Iceberg do it in
Java, where a shade plugin makes it one Maven problem, and this project's core is Python.

Glue has native Iceberg through `--datalake-formats=iceberg`. No classpath to assemble.

**The merge key is (meter_id, interval_start), and that is doctrine 4 in one clause.** A
restatement carries the same key with a higher revision, so it *updates* the row instead of
appending beside it — and `supersedes`, `restatement_cause` and the prior `revision` survive on
the row, which is what makes the correction recoverable rather than a silent overwrite.

**The guard on revision matters.** `WHEN MATCHED AND source.revision > target.revision` means a
file replayed after a later correction cannot walk the value backwards. Glue jobs are retried,
S3 lists are eventually consistent, and a merge that is not ordered by revision is a merge that
is correct only if nothing is ever delivered twice.
"""

from __future__ import annotations

import sys

import boto3
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

ARGUMENTS = getResolvedOptions(sys.argv, ["JOB_NAME", "WAREHOUSE", "LANDING", "DATABASE", "TABLE"])

spark = SparkContext.getOrCreate() and GlueContext(SparkContext.getOrCreate()).spark_session

# The catalog is configured by the *job definition*, not here.
#
# `spark.sql.extensions` is a static config: Spark refuses it after the session exists —
# "Cannot modify the value of a static config" — and Iceberg's MERGE syntax comes from that
# extension, so setting it late means a session that cannot parse the one statement this job
# exists to run. `infra/lakehouse/maintenance.tf` passes it as `--conf` where Glue applies it
# before the session is built.

job = Job(GlueContext(SparkContext.getOrCreate()))
job.init(ARGUMENTS["JOB_NAME"], ARGUMENTS)

TARGET = f"glue_catalog.{ARGUMENTS['DATABASE']}.{ARGUMENTS['TABLE']}"

# **The writer creates the table, because nothing else can.**
#
# This used to be an `aws_glue_catalog_table` in `infra/lakehouse/glue.tf` carrying
# `table_type = "ICEBERG"`, and it produced a catalogue entry with no metadata location, which
# is not an Iceberg table and cannot be made into one. Athena says so in as many words:
#
#     GENERIC_USER_ERROR: Detected Iceberg type table without metadata location. [...] Setting
#     table_type parameter in Glue metastore to create an Iceberg table is not supported.
#
# An Iceberg table *is* its metadata, and the metadata is written by the engine that creates
# it. Terraform can declare a database, a location, a bucket and a key; it cannot write a
# manifest. So the schema moved here, to the job that writes the rows — see ADR-0008. The
# databases, the warehouse location and the encryption are still Terraform's, and the property
# list below is the same one every table in this lakehouse gets.
#
# `IF NOT EXISTS`, so this is idempotent: it runs on every merge, and `deploy.yml` runs the job
# once with nothing landed for the sole purpose of bringing the table into existence before the
# governance layer attaches a data quality ruleset to it.
spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {TARGET} (
        meter_id              STRING,
        interval_start        TIMESTAMP,
        energy_wh             BIGINT,
        readings              INT,
        duplicates_suppressed INT,
        corrections_absorbed  INT,
        closed_at             TIMESTAMP,
        first_seen_at         TIMESTAMP,
        watermark_status      STRING,
        idle_partitions       ARRAY<STRING>,
        revision              INT,
        supersedes            BIGINT,
        restatement_cause     STRING,
        lineage_id            STRING,
        merged_at             TIMESTAMP
    )
    USING iceberg
    PARTITIONED BY (days(interval_start))
    LOCATION '{ARGUMENTS["WAREHOUSE"]}/silver/{ARGUMENTS["TABLE"]}'
    TBLPROPERTIES (
        'format-version' = '2',
        'write.format.default' = 'parquet',
        'write.parquet.compression-codec' = 'zstd',
        'write.delete.mode' = 'merge-on-read',
        'write.update.mode' = 'merge-on-read',
        'write.merge.mode' = 'merge-on-read',
        'history.expire.max-snapshot-age-ms' = '2592000000',
        'history.expire.min-snapshots-to-keep' = '10'
    )
""")

# **Asked before it is read, not caught after.**
#
# `landed.rdd.isEmpty()` was the guard, and it cannot run: on an estate where no capture has
# happened yet the landing prefix does not exist at all, and `spark.read.json` raises
# `AnalysisException: Path does not exist` before there is a DataFrame to ask. That is precisely
# the deploy-time run — the one whose only purpose is to create the table above — so the guard
# was unreachable on the single occasion it was written for.
#
# `list_objects_v2` rather than catching the exception, because the exception's import path moved
# between Spark 3.3 and 3.4 (`pyspark.sql.utils` to `pyspark.errors`) and a job that catches the
# wrong class on a Glue version upgrade fails in a way that reads like a data problem. An empty
# prefix and an absent prefix are the same fact to S3, and the same fact to this job.
bucket, _, prefix = ARGUMENTS["LANDING"].removeprefix("s3://").partition("/")
listing = boto3.client("s3").list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)

#: Whether there is anything to merge. Not an error when there is not: a capture that closed no
#: window has nothing to add, and a job that failed on that would fail every time the stream was
#: quiet — and on the deploy-time run whose only purpose is the CREATE TABLE above.
#:
#: **Expressed as a branch and not as an early exit.** It was `job.commit()` followed by
#: `raise SystemExit(0)`, and Glue reported the run FAILED with `SystemExit: 0` in the error
#: field. Glue's wrapper treats any `SystemExit` escaping the script as an abnormal end and does
#: not read the code — so a job that had done exactly what it was asked to do reported failure,
#: and the deploy step that waits on it stopped the whole apply.
HAS_LANDED = listing.get("KeyCount", 0) > 0

if not HAS_LANDED:
    print(f"nothing landed under {ARGUMENTS['LANDING']}; {TARGET} exists, no merge")
else:
    spark.read.json(ARGUMENTS["LANDING"]).createOrReplaceTempView("landed")

    # Quarantines are evidence, not settlement rows. They land in the same stream because they are
    # produced by the same operator, and they belong in their own table rather than as nulls here.
    # **Every column the core computed, and `closed_at` is the one that matters.**
    #
    # This view used to select eight columns and set `closed_at` to `CURRENT_TIMESTAMP()` — the time
    # the *merge* ran, not the watermark that permitted publication. Those are different facts and
    # only one of them is evidence: a row whose `closed_at` precedes its own interval end could not
    # have come from the core, which is what makes claim 1 checkable in SQL after the fact. Stamping
    # it with the merge clock made the column always pass and mean nothing.
    #
    # It now comes off the record, where `streaming/operators.py` puts it. `merged_at` is the merge
    # clock, kept as its own column because dbt's incremental predicate needs to know what this run
    # touched, and that is a different question from when the window closed.
    spark.sql("""
        CREATE OR REPLACE TEMPORARY VIEW closed AS
        SELECT
            meter                                              AS meter_id,
            TIMESTAMP_MILLIS(CAST(interval_start AS BIGINT))   AS interval_start,
            CAST(energy_wh AS BIGINT)                          AS energy_wh,
            CAST(readings AS INT)                              AS readings,
            CAST(duplicates_suppressed AS INT)                 AS duplicates_suppressed,
            CAST(corrections_absorbed AS INT)                  AS corrections_absorbed,
            TIMESTAMP_MILLIS(CAST(closed_at AS BIGINT))        AS closed_at,
            TIMESTAMP_MILLIS(CAST(first_seen_at AS BIGINT))    AS first_seen_at,
            watermark_status,
            idle_partitions,
            CAST(revision AS INT)                              AS revision,
            CAST(supersedes AS BIGINT)                         AS supersedes,
            restatement_cause,
            lineage_id,
            CURRENT_TIMESTAMP()                                AS merged_at
        FROM landed
        -- Three kinds share this prefix because one operator produces all three. `published`,
        -- `restated` and `confirmed` are rows; `quarantine` is a refusal and belongs in its own
        -- table; `watermark` is a condition report with no meter at all, which is why the second
        -- clause is not redundant with the first.
        WHERE kind NOT IN ('quarantine', 'watermark') AND meter IS NOT NULL
    """)

    spark.sql(f"""
        MERGE INTO {TARGET} AS target
        USING closed AS source
        ON  target.meter_id = source.meter_id
        AND target.interval_start = source.interval_start
        WHEN MATCHED AND source.revision > target.revision THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)

    merged = spark.sql(f"SELECT COUNT(*) AS rows, MAX(revision) AS top FROM {TARGET}").collect()[0]
    print(f"merged; {TARGET} now holds {merged['rows']} rows, highest revision {merged['top']}")

job.commit()
