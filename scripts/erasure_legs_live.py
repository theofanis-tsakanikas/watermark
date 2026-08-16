#!/usr/bin/env python3
"""Ask the estate what the erasure certificate claims, through different calls than it made.

Claim 6 says the system refuses to report "erased" unless every leg is confirmed. The state
machine enforces that on itself. This enforces it on the state machine — which is the half that
was missing, because a certificate that verifies its own legs is a signature on a blank page.

Every leg is checked against a different service than the one that performed it where that is
possible: the shred through KMS rather than through the step's output, the online store through
`GetRecord` rather than through the delete's response, the lakehouse and the training sets
through Athena rather than through the Glue job's exit code.

**Run it after the erasure, in the same job, and let it fail the job.** All decision logic lives
in `watermark.erasure.verify` and is exercised offline; this file goes and looks, and does no
deciding of its own beyond turning an exception into "unobservable".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from watermark.erasure.verify import (  # noqa: E402
    Finding,
    Observation,
    report,
    residual_from_certificate,
)

#: The legs `ErasureScope.legs` declares, restated as the thing this script must cover. Imported
#: would be better; it is not, because the scope needs a loaded contract set and this script must
#: run in a job that has the repository but not necessarily a resolvable contract root. The test
#: `test_erasure_legs_live.py` asserts the two lists are identical, so the copy cannot drift.
EXPECTED_LEGS = (
    "crypto_shred",
    "lakehouse_rows",
    "offline_store",
    "online_store",
    "training_sets",
    "model_artefacts",
)


#: A result set is a header row plus its rows. Fewer than two means the query returned no row at
#: all, which is a different answer from a row containing zero.
HEADER_AND_ONE_ROW = 2


def _athena_count(
    client, workgroup: str, database: str, query: str, parameters: list[str]
) -> tuple[int | None, str | None]:
    """One scalar count, or a reason it could not be taken. Never a zero it did not measure.

    The values are **bound, not interpolated** — the same rule `queries/` is held to. A subject
    id arrives from a request and reaches a `WHERE` clause; that is the shape of the injection
    whatever the id happens to look like today.
    """
    try:
        started = client.start_query_execution(
            QueryString=query,
            WorkGroup=workgroup,
            QueryExecutionContext={"Database": database},
            ExecutionParameters=parameters,
        )
    except ClientError as error:
        return None, f"Athena refused the query: {error.response['Error']['Code']}"

    execution = started["QueryExecutionId"]
    waiter_state = ""
    while waiter_state not in ("SUCCEEDED", "FAILED", "CANCELLED"):
        detail = client.get_query_execution(QueryExecutionId=execution)["QueryExecution"]
        waiter_state = detail["Status"]["State"]
    if waiter_state != "SUCCEEDED":
        reason = detail["Status"].get("StateChangeReason", "no reason given")
        return None, f"the count did not run: {reason}"

    rows = client.get_query_results(QueryExecutionId=execution)["ResultSet"]["Rows"]
    if len(rows) < HEADER_AND_ONE_ROW:
        return None, "the count returned no rows at all, which is not a count of zero"
    return int(rows[1]["Data"][0]["VarCharValue"]), None


def _athena_rows(
    client, workgroup: str, database: str, query: str, parameters: list[str]
) -> tuple[list[list[str]], str | None]:
    """The rows themselves, for when a count is not an answer.

    A leg that reports "four rows survived" and stops has spent a whole capture to say that
    something is wrong without saying what — the same lesson the live case matrix and the dbt
    tests each learnt on their own.
    """
    try:
        started = client.start_query_execution(
            QueryString=query,
            WorkGroup=workgroup,
            QueryExecutionContext={"Database": database},
            ExecutionParameters=parameters,
        )
    except ClientError as error:
        return [], f"Athena refused the query: {error.response['Error']['Code']}"

    execution = started["QueryExecutionId"]
    state = ""
    while state not in ("SUCCEEDED", "FAILED", "CANCELLED"):
        detail = client.get_query_execution(QueryExecutionId=execution)["QueryExecution"]
        state = detail["Status"]["State"]
    if state != "SUCCEEDED":
        return [], detail["Status"].get("StateChangeReason", "no reason given")

    rows = client.get_query_results(QueryExecutionId=execution)["ResultSet"]["Rows"]
    return [[cell.get("VarCharValue", "") for cell in row["Data"]] for row in rows[1:]], None


def _crypto_shred(subject: str, project: str, bucket: str) -> Observation:
    """KMS directly, plus the durable marker that separates 'destroyed' from 'never existed'."""
    kms = boto3.client("kms")
    state: str | None = None
    try:
        key = kms.describe_key(KeyId=f"alias/{project}-subject-{subject}")
        state = key["KeyMetadata"]["KeyState"]
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("NotFoundException", "AccessDeniedException"):
            return Observation(
                leg="crypto_shred",
                unobservable_because=f"KMS answered {error.response['Error']['Code']}",
            )
        if error.response["Error"]["Code"] == "AccessDeniedException":
            return Observation(
                leg="crypto_shred",
                unobservable_because=(
                    "this role may not describe the subject's key, so the shred cannot be "
                    "confirmed from here. Widening the role to check is the wrong fix; run the "
                    "check with a role that already has it."
                ),
            )

    marker = False
    try:
        boto3.client("s3").head_object(Bucket=bucket, Key=f"erasure-shredded/{subject}.json")
        marker = True
    except ClientError:
        marker = False

    return Observation(leg="crypto_shred", key_state=state, shred_marker=marker)


def _online_store(subject: str, meters: list[str], project: str) -> Observation:
    """`GetRecord` per meter. A record that comes back is a record the delete leg did not take."""
    if not meters:
        return Observation(
            leg="online_store",
            unobservable_because=(
                "no meter was named for this subject, so there was nothing to ask the online "
                "store about. An empty question is not an empty answer."
            ),
        )

    runtime = boto3.client("sagemaker-featurestore-runtime")
    sagemaker = boto3.client("sagemaker")
    groups = [
        group["FeatureGroupName"]
        for group in sagemaker.list_feature_groups(NameContains=project).get(
            "FeatureGroupSummaries", []
        )
    ]
    if not groups:
        return Observation(
            leg="online_store",
            unobservable_because=(
                "no feature group exists, so a delete against the online store would succeed "
                "having done nothing. This is the shape the leg fails in silently."
            ),
        )

    survivors = 0
    for group in groups:
        for meter in meters:
            try:
                answer = runtime.get_record(
                    FeatureGroupName=group, RecordIdentifierValueAsString=meter
                )
            except ClientError as error:
                if error.response["Error"]["Code"] in ("ResourceNotFound", "ValidationError"):
                    continue
                return Observation(
                    leg="online_store",
                    unobservable_because=f"GetRecord answered {error.response['Error']['Code']}",
                )
            if answer.get("Record"):
                survivors += 1
    return Observation(leg="online_store", rows=survivors)


def _certificate_residual(subject: str, bucket: str) -> Observation:
    """The bounded leg. What is checked is that the certificate says what it cannot do."""
    s3 = boto3.client("s3")
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=f"erasure-certificates/{subject}/")
    objects = listing.get("Contents", [])
    if not objects:
        return Observation(
            leg="model_artefacts",
            unobservable_because="no certificate was written, so it declares no residual",
        )

    newest = max(objects, key=lambda item: item["LastModified"])
    body = s3.get_object(Bucket=bucket, Key=newest["Key"])["Body"].read()
    return residual_from_certificate(body)


def _offline_store(arguments, athena) -> Observation:
    """The offline store, through the table the feature group says it writes.

    SageMaker creates the Glue table for an Iceberg-format offline store and names it itself;
    `DescribeFeatureGroup` is the only thing that knows what it called it. Asking the feature
    group rather than assuming is also what makes this leg independent — the delete leg acts on
    the online store, and this reads what the offline half actually holds.
    """
    import boto3  # noqa: PLC0415

    sagemaker = boto3.client("sagemaker")
    group = f"{arguments.project}-meter-consumption"
    try:
        detail = sagemaker.describe_feature_group(FeatureGroupName=group)
    except ClientError as error:
        return Observation(
            leg="offline_store",
            unobservable_because=f"DescribeFeatureGroup answered {error.response['Error']['Code']}",
        )

    catalog = (detail.get("OfflineStoreConfig") or {}).get("DataCatalogConfig") or {}
    database, table = catalog.get("Database"), catalog.get("TableName")
    if not database or not table:
        return Observation(
            leg="offline_store",
            unobservable_because=(
                f"`{group}` declares no offline store catalogue entry, so there is no table to "
                f"count. A feature group with no offline store is a training set nobody can "
                f"reproduce, which is a different finding from an erasure that missed."
            ),
        )

    # **Through the assignment history, not by meter** — the same correction the lakehouse leg
    # needed a day earlier, and it would have gone the same way here. The offline store holds a
    # *history* of feature values, so a row belongs to whoever held the meter at that instant;
    # `M00007` changes customer at 10:00. Counting by meter would report the predecessor's rows
    # as survivors and demand an over-deletion the erasure is right to refuse.
    #
    # The event time is a String with nine fractional digits — the one shape an Iceberg offline
    # store accepts — so it is narrowed to milliseconds before it can meet a timestamp. The
    # state machine's DELETE is written the same way, which is deliberate: the two must agree
    # about what belongs to whom, or one of them is checking a different question.
    moment = "cast(from_iso8601_timestamp(substr(f.event_time, 1, 23) || 'Z') as timestamp)"
    # **`is_deleted` is excluded, and it is not a loophole.**
    #
    # SageMaker's `DeleteRecord` is a soft delete: it appends a row to the offline store with
    # `is_deleted = true` and no feature values, which is how that store records that a record
    # stopped existing. The online-store leg issues exactly that call, so the erasure's own
    # deletion flushes through as a row carrying the meter and the instant — and a count that
    # did not know the difference reads the record of the removal as the thing that survived it.
    #
    # What is being asked is whether a *feature value* belonging to the subject is still
    # readable. A tombstone is the opposite of one. The diagnostic below prints the flag anyway,
    # because this argument only holds while the rows really are tombstones.
    belonging = f"""
        FROM {database}.{table} f
        JOIN {arguments.gold}.meter_assignment_scd2 a ON a.meter_id = f.meter_id
        WHERE a.customer_id = ?
          AND NOT coalesce(f.is_deleted, false)
          AND {moment} >= a.valid_from
          AND (a.valid_to IS NULL OR {moment} < a.valid_to)
    """
    count, why = _athena_count(
        athena, arguments.workgroup, database, f"SELECT count(*) {belonging}", [arguments.subject]
    )
    if not count:
        return Observation(leg="offline_store", rows=count, unobservable_because=why)

    # **Name the survivors, and say when they were written.**
    #
    # The offline store is *eventually* consistent: `PutRecord` answers from the online store and
    # SageMaker flushes to the offline one on its own schedule. So a record already in flight when
    # the erasure ran lands afterwards, and the leg deleted everything that existed at the moment
    # it looked. That is a different finding from a DELETE whose predicate is wrong, and the two
    # are indistinguishable from a count — which is why the first run of this leg reported four
    # survivors and said nothing about them.
    survivors, _ = _athena_rows(
        athena,
        arguments.workgroup,
        database,
        f"SELECT f.meter_id, f.event_time, f.write_time, f.is_deleted {belonging}"
        " ORDER BY f.event_time LIMIT 5",
        [arguments.subject],
    )
    written = "; ".join(" ".join(row) for row in survivors) or "could not be listed"
    return Observation(
        leg="offline_store",
        rows=count,
        unobservable_because=None,
        note=f"survivors (meter, event_time, write_time, is_deleted): {written}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--meters", default="", help="comma-separated, the subject's meters")
    parser.add_argument("--project", default="watermark")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--workgroup", required=True)
    parser.add_argument("--silver", default="watermark_silver")
    parser.add_argument("--gold", default="watermark_gold")
    arguments = parser.parse_args(argv)

    meters = [meter for meter in arguments.meters.split(",") if meter]
    athena = boto3.client("athena")
    observations = [_crypto_shred(arguments.subject, arguments.project, arguments.bucket)]

    # **Rows belonging to the subject, resolved the way the erasure resolves them.**
    #
    # The first live run of this check reported 54 surviving rows on `silver.meter_interval` and
    # it was wrong — the count was `WHERE meter_id IN (…)`, which is every reading the meter ever
    # produced. `M00007` changes customer at 10:00, so more than half those rows belong to the
    # *predecessor* and must survive. Demanding their deletion is asking for the over-deletion
    # `DeleteRowsPhysically` goes out of its way to avoid, and the certificate step beside this
    # one had that right all along. The same lesson as the replay counts: the assertion and the
    # thing it asserts about must agree on the question.
    #
    # The join is the assignment history, half-open on the right, exactly as the state machine's
    # own DELETE writes it.
    # The database names are this script's own arguments and the only outside value — the
    # subject id — is bound below.
    owned_by_subject = f"""
        SELECT count(*)
        FROM {arguments.silver}.meter_interval r
        JOIN {arguments.gold}.meter_assignment_scd2 a ON a.meter_id = r.meter_id
        WHERE a.customer_id = ?
          AND r.interval_start >= a.valid_from
          AND (a.valid_to IS NULL OR r.interval_start < a.valid_to)
    """  # noqa: S608
    count, why = _athena_count(
        athena, arguments.workgroup, arguments.silver, owned_by_subject, [arguments.subject]
    )
    observations.append(Observation(leg="lakehouse_rows", rows=count, unobservable_because=why))

    # The offline store's table is named by the feature group, not by this file. Guessing it
    # produced `TABLE_NOT_FOUND: watermark_gold.meter_interval_features` — a table that has
    # never existed — and an unobservable leg, which is at least honest, but it is a leg nobody
    # was checking dressed as a leg somebody was.
    offline = _offline_store(arguments, athena)
    observations.append(offline)

    count, why = _athena_count(
        athena,
        arguments.workgroup,
        arguments.gold,
        f"SELECT count(*) FROM {arguments.gold}.training_snapshot WHERE customer_id = ?",  # noqa: S608
        [arguments.subject],
    )
    observations.append(Observation(leg="training_sets", rows=count, unobservable_because=why))

    observations.append(_online_store(arguments.subject, meters, arguments.project))
    observations.append(_certificate_residual(arguments.subject, arguments.bucket))

    verdicts = report(observations, EXPECTED_LEGS)

    print(f"\nevery leg of the erasure of {arguments.subject}, checked independently:\n")
    print("| leg | finding | what was seen |")
    print("|---|---|---|")
    for item in verdicts:
        print(f"| `{item.leg}` | **{item.finding.value}** | {item.detail} |")

    failed = [item for item in verdicts if not item.ok]
    if failed:
        print()
        for item in failed:
            print(f"::error::{item.leg}: {item.finding.value} — {item.detail}")
        unobservable = [item for item in failed if item.finding is Finding.UNOBSERVABLE]
        if unobservable:
            print(
                "::error::a leg that could not be observed is not a leg that passed. The "
                "certificate says erased; this run cannot confirm it."
            )
        return 1

    print(
        f"\nall {len(verdicts)} legs confirmed against the estate, independently of the "
        f"certificate that claims them"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
