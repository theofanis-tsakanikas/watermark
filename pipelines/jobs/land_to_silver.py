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
import time

import boto3
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
)

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

#: How many times the merge may lose a commit race before it is contention worth reporting.
MERGE_ATTEMPTS = 4
MERGE_BACKOFF_SECONDS = 15

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


def _metadata_is_missing() -> bool:
    """Whether the catalogue points at an Iceberg metadata file that is not there.

    **A narrow rule, and the narrowness is the point.** An Iceberg table is its metadata: the
    catalogue entry is a pointer, and if the object it names has gone then nothing in the table
    can be read, no snapshot can be resolved, and every statement against it fails with an error
    about the pointer rather than about the data. `CREATE TABLE IF NOT EXISTS` sees a table and
    steps aside; the `MERGE` below then fails on a file that does not exist.

    Recreating in that state is not data loss — there is no reachable data to lose. It is
    removing a tombstone. The condition is deliberately not "the table looks wrong" or "the
    schema has drifted", either of which would make this a job that silently rewrites a
    settlement table; it is exactly "the pointer resolves to nothing".

    It happens for dull reasons: a lifecycle rule that expired warehouse objects, a partial
    destroy, a prefix cleared by hand between captures. All three leave the same wreck.
    """
    table = boto3.client("glue").get_table(
        DatabaseName=ARGUMENTS["DATABASE"], Name=ARGUMENTS["TABLE"]
    )["Table"]
    location = table.get("Parameters", {}).get("metadata_location")
    if not location:
        return False
    metadata_bucket, _, metadata_key = location.removeprefix("s3://").partition("/")
    try:
        boto3.client("s3").head_object(Bucket=metadata_bucket, Key=metadata_key)
    except boto3.client("s3").exceptions.ClientError:
        print(f"{location} is gone; the catalogue entry points at nothing")
        return True
    return False


try:
    if _metadata_is_missing():
        # **The catalogue entry is deleted directly, not through `DROP TABLE`.** Spark's Iceberg
        # catalog loads a table before it drops it — it has to, in order to purge the metadata it
        # is about to orphan — so `DROP TABLE IF EXISTS` against a table whose metadata has gone
        # fails on the same missing object as the merge would:
        #
        #     An error occurred while calling o112.sql. The specified key does not exist.
        #     (Service: S3, Status Code: 404)
        #
        # There is nothing to purge. What is left is a row in the Glue catalogue, and that is
        # what `glue:DeleteTable` removes.
        print(f"recreating {TARGET}: its metadata is unreachable, so it holds nothing readable")
        boto3.client("glue").delete_table(
            DatabaseName=ARGUMENTS["DATABASE"], Name=ARGUMENTS["TABLE"]
        )
except boto3.client("glue").exceptions.EntityNotFoundException:
    pass  # No table at all is the ordinary first-run case; the CREATE below handles it.

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
    # **The schema is declared, and the lookup is explicitly recursive.** Two failures, one read.
    #
    # `spark.read.json(path)` returned `AnalysisException: Unable to infer schema for JSON` on a
    # prefix holding twenty-two committed part files. Flink's `FileSink` buckets by hour, so
    # every file sits one directory down — `landing/meter_interval/2026-08-12--00/windows-….jsonl`
    # — and the directory name is not `key=value`, so it is not a partition and the default
    # listing does not descend into it. Nothing was read, and "nothing was read" surfaces as a
    # schema error rather than an empty frame.
    #
    # Inference had to go anyway, and this is the more important half. The landed lines are a
    # union of three shapes, and the columns of the rarest one decide the types: a capture with
    # no restatement has `supersedes` null on every line, Spark types it `string`, and the
    # `MERGE` fails on a type mismatch against a `bigint` column — on the capture that happened
    # to be quiet, not on the one that introduced the bug. A declared schema makes the read mean
    # the same thing every time, which is the whole of what claim 2 asks of a pipeline.
    LANDED = StructType(
        [
            StructField("kind", StringType()),
            StructField("meter", StringType()),
            StructField("interval_start", LongType()),
            # A string on the wire, deliberately: JSON numbers are doubles in most readers and
            # `energy_wh` is an exact count of watt-hours. Cast to `bigint` in the view below.
            StructField("energy_wh", StringType()),
            StructField("readings", IntegerType()),
            StructField("duplicates_suppressed", IntegerType()),
            StructField("corrections_absorbed", IntegerType()),
            StructField("closed_at", LongType()),
            StructField("first_seen_at", LongType()),
            StructField("revision", IntegerType()),
            StructField("supersedes", LongType()),
            StructField("restatement_cause", StringType()),
            StructField("watermark_status", StringType()),
            StructField("idle_partitions", ArrayType(StringType())),
            StructField("lineage_id", StringType()),
            StructField("observed_status", StringType()),
            # The watermark condition lines and the quarantine lines share this file. They are
            # filtered out below; their columns are declared so that a line the schema does not
            # describe is not silently dropped by `PERMISSIVE` mode.
            StructField("status", StringType()),
            StructField("holding_back", StringType()),
            StructField("lag_millis", LongType()),
            StructField("watermark", LongType()),
            StructField("may_close_windows", BooleanType()),
            StructField("at", LongType()),
            StructField("reason", StringType()),
            StructField("detail", StringType()),
            StructField("partition", StringType()),
            StructField("source", StringType()),
        ]
    )

    landed = (
        spark.read.option("recursiveFileLookup", "true").schema(LANDED).json(ARGUMENTS["LANDING"])
    )
    landed.createOrReplaceTempView("landed")

    # S3 said there were objects; the reader has to agree. Without this the two failures above
    # would have produced an empty frame, a merge of nothing, and a job that reported success —
    # which is the failure this repository exists to argue against, in the job that stores the
    # evidence for it.
    read = landed.count()
    print(f"read {read} landed lines from {ARGUMENTS['LANDING']}")
    if read == 0:
        raise ValueError(
            f"{ARGUMENTS['LANDING']} holds objects and the reader produced no rows. "
            "A merge of nothing must not be reported as a merge."
        )

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
    # **One row per key reaches the MERGE, and that is not an optimisation.**
    #
    # The merge matches on `(meter_id, interval_start)`. A capture that produces a restatement
    # lands *both* statements about that interval — revision 0 when the window closed and
    # revision 1 when the head-end's file corrected it — so the source holds two rows for one
    # key and Spark refuses:
    #
    #     The ON search condition of the MERGE statement matched a single row from the target
    #     table with multiple rows of the source table. This could result in the target row
    #     being operated on more than once [...] and is not allowed.
    #
    # It is right to refuse: applying both in one statement leaves the outcome depending on
    # which arrived last inside one transaction, which is the shape of bug doctrine 4 exists to
    # rule out. The answer is the same one `queries/settlement_hourly.sql` already gives — the
    # newest revision wins — applied before the merge instead of after it.
    #
    # This could not have surfaced until restatements did. Every earlier run produced none: the
    # corrections were refused as too late, so every key appeared exactly once and the merge
    # looked correct for four captures in a row.
    #
    # The tie-break is total, because two rows can share a revision. A `confirmed` says late
    # data arrived and the number did not move, and it carries the same revision as the
    # publication it confirms. Ordering by `closed_at` and then `lineage_id` makes the choice
    # between them deterministic rather than dependent on file order — which claim 2 requires
    # and a partitioned read would otherwise decide by accident.
    spark.sql("""
        CREATE OR REPLACE TEMPORARY VIEW newest AS
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY meter, interval_start
                ORDER BY revision DESC, closed_at DESC, lineage_id DESC
            ) AS rank_in_key
            FROM landed
            WHERE kind NOT IN ('quarantine', 'watermark') AND meter IS NOT NULL
        )
        WHERE rank_in_key = 1
    """)

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
        -- Three kinds share the landing prefix because one operator produces all three.
        -- `published`, `restated` and `confirmed` are rows; `quarantine` is a refusal and
        -- belongs in its own table; `watermark` is a condition report with no meter at all,
        -- which is why the filter in `newest` needs both clauses.
        FROM newest
    """)

    # **The merge retries, because a concurrent writer is not an error.**
    #
    # Iceberg plans a row-level delete against the data files of the snapshot it read, and
    # validates at commit that those files still exist. Compaction rewrites exactly those files.
    # Run the two together and the commit is refused:
    #
    #     ValidationException: Cannot commit, missing data files: [...00000-18-....parquet, ...]
    #
    # That is what happened: `watermark-compaction` was scheduled `cron(30 * * * ? *)` and this
    # job started at 03:30:47. Neither had done anything wrong. Optimistic concurrency means the
    # loser re-reads and tries again, and a writer that treats the first refusal as a failure has
    # simply not implemented the protocol.
    #
    # `closed` is a view over the landing files, not over the table, so re-running the statement
    # re-plans against the new snapshot and the same rows land. Bounded, because a merge that
    # cannot commit after this many attempts is contention nobody should absorb silently.
    merge = f"""
        MERGE INTO {TARGET} AS target
        USING closed AS source
        ON  target.meter_id = source.meter_id
        AND target.interval_start = source.interval_start
        WHEN MATCHED AND source.revision > target.revision THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """
    for attempt in range(1, MERGE_ATTEMPTS + 1):
        try:
            spark.sql(merge)
            break
        except Exception as error:  # py4j wraps the Iceberg exception; the text is what is left
            retryable = "missing data files" in str(error) or "ValidationException" in str(error)
            if not retryable or attempt == MERGE_ATTEMPTS:
                raise
            print(
                f"merge attempt {attempt} lost a commit race with a concurrent writer; "
                f"re-planning against the new snapshot"
            )
            time.sleep(MERGE_BACKOFF_SECONDS * attempt)

    merged = spark.sql(f"SELECT COUNT(*) AS rows, MAX(revision) AS top FROM {TARGET}").collect()[0]
    print(f"merged; {TARGET} now holds {merged['rows']} rows, highest revision {merged['top']}")

job.commit()
