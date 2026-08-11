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

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

ARGUMENTS = getResolvedOptions(sys.argv, ["JOB_NAME", "WAREHOUSE", "LANDING", "DATABASE", "TABLE"])

spark = (
    SparkContext.getOrCreate()
    and GlueContext(SparkContext.getOrCreate()).spark_session
)

# The catalog is configured by the *job definition*, not here.
#
# `spark.sql.extensions` is a static config: Spark refuses it after the session exists —
# "Cannot modify the value of a static config" — and Iceberg's MERGE syntax comes from that
# extension, so setting it late means a session that cannot parse the one statement this job
# exists to run. `infra/lakehouse/maintenance.tf` passes it as `--conf` where Glue applies it
# before the session is built.

job = Job(GlueContext(SparkContext.getOrCreate()))
job.init(ARGUMENTS["JOB_NAME"], ARGUMENTS)

landed = spark.read.json(ARGUMENTS["LANDING"])

if landed.rdd.isEmpty():
    # Not an error. A capture that closed no window has nothing to merge, and a job that failed
    # on that would fail every time the stream was quiet.
    print("nothing landed; no merge")
    job.commit()
    raise SystemExit(0)

landed.createOrReplaceTempView("landed")

# Quarantines are evidence, not settlement rows. They land in the same stream because they are
# produced by the same operator, and they belong in their own table rather than as nulls here.
spark.sql("""
    CREATE OR REPLACE TEMPORARY VIEW closed AS
    SELECT
        meter                                          AS meter_id,
        TIMESTAMP_MILLIS(CAST(interval_start AS BIGINT)) AS interval_start,
        CAST(energy_wh AS BIGINT)                      AS energy_wh,
        CAST(revision AS INT)                          AS revision,
        CAST(supersedes AS BIGINT)                     AS supersedes,
        restatement_cause,
        watermark_status,
        CURRENT_TIMESTAMP()                            AS closed_at
    FROM landed
    WHERE kind <> 'quarantine' AND meter IS NOT NULL
""")

target = f"glue_catalog.{ARGUMENTS['DATABASE']}.{ARGUMENTS['TABLE']}"

spark.sql(f"""
    MERGE INTO {target} AS target
    USING closed AS source
    ON  target.meter_id = source.meter_id
    AND target.interval_start = source.interval_start
    WHEN MATCHED AND source.revision > target.revision THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
""")

merged = spark.sql(f"SELECT COUNT(*) AS rows, MAX(revision) AS top FROM {target}").collect()[0]
print(f"merged; {target} now holds {merged['rows']} rows, highest revision {merged['top']}")

job.commit()
