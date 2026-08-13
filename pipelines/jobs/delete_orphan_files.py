"""Remove data files no Iceberg snapshot references any more.

The third of the three routines ADR-0002 kept. It runs after compaction in the erasure
orchestration, because compaction is what *makes* the files holding a subject unreferenced —
and claim 6's certificate cites the run that actually removed them rather than predicting that
a managed service will get to it.

`OLDER_THAN_DAYS` is three, and it is a floor rather than a preference: a shorter window risks
deleting a file a long-running write has produced and not yet committed, which turns a slow job
into a corrupt table.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from awsglue.context import GlueContext  # type: ignore[import-not-found]
from awsglue.utils import getResolvedOptions  # type: ignore[import-not-found]
from pyspark.context import SparkContext  # type: ignore[import-not-found]

TABLES = (
    ("watermark_silver", "meter_interval"),
    ("watermark_gold", "settlement_hour"),
    ("watermark_bronze", "quarantine"),
)

#: Never go below this, whatever the argument says. An in-flight write is invisible to orphan
#: detection, and the failure is silent data loss rather than an error.
MINIMUM_AGE_DAYS = 3


#: `timezone.utc`, not `datetime.UTC`, and the difference is which Python this file runs on.
#:
#: This repository targets 3.12. **Glue 4.0 runs 3.10**, where `datetime.UTC` does not exist:
#:
#:     ImportError: cannot import name 'UTC' from 'datetime'
#:
#: Nothing local catches that — `ruff`, `mypy` and the test suite all read this file with the
#: interpreter the laptop has. `scripts/check_glue_runtime.py` is what catches it now.
#:
#: How the cutoff instant reaches a `CALL`, and why it is computed here rather than in SQL.
#:
#: Iceberg's stored-procedure grammar takes **literals only**. `TIMESTAMPADD(DAY, -3,
#: CURRENT_TIMESTAMP)` — the obvious way to say "three days ago", and valid Spark SQL anywhere
#: else — is rejected at parse time:
#:
#:     AnalysisException: mismatched input '(' expecting STRING
#:
#: So the arithmetic happens in Python and a typed literal goes into the statement. That has a
#: consequence worth naming: the cutoff is the moment the *driver* computed it, not the moment
#: the procedure runs, and the two differ by however long the job takes to start. Minutes, on a
#: floor of three days. It would matter if the floor were minutes, and `MINIMUM_AGE_DAYS` is
#: what stops anyone setting it there.
def cutoff(days: int) -> str:
    """A SQL timestamp literal `days` before now, in the form Iceberg's parser accepts."""
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return f"TIMESTAMP '{moment.strftime('%Y-%m-%d %H:%M:%S')}'"


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
        raise RuntimeError("no table in TABLES exists; refusing to report a maintenance run")
    return present


def main() -> int:
    arguments = getResolvedOptions(sys.argv, ["WAREHOUSE", "OLDER_THAN_DAYS"])
    age = max(MINIMUM_AGE_DAYS, int(arguments["OLDER_THAN_DAYS"]))
    if age != int(arguments["OLDER_THAN_DAYS"]):
        print(
            f"OLDER_THAN_DAYS was {arguments['OLDER_THAN_DAYS']}; raised to {age}. Below that, "
            "an in-flight write's files look orphaned and deleting them corrupts the table.",
            file=sys.stderr,
        )

    spark = GlueContext(SparkContext.getOrCreate()).spark_session
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse", arguments["WAREHOUSE"])

    for database, table in existing(spark, TABLES):
        result = spark.sql(
            f"CALL glue_catalog.system.remove_orphan_files("
            f"  table => '{database}.{table}',"
            f"  older_than => {cutoff(age)}"
            f")"
        ).collect()
        print(f"orphans {database}.{table}: removed {len(result)} files")

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
