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

    # The lakehouse and the offline store are the same Iceberg tables read two ways: the rows a
    # settlement resolves, and the feature values a training run would read back. They are
    # separate legs because they fail separately — a delete that misses the feature table leaves
    # the subject out of every report and inside every model.
    for leg, database, table, column in (
        ("lakehouse_rows", arguments.silver, "meter_interval", "meter_id"),
        ("offline_store", arguments.gold, "meter_interval_features", "meter_id"),
        ("training_sets", arguments.gold, "training_snapshot", "customer_id"),
    ):
        if column == "meter_id" and not meters:
            observations.append(
                Observation(
                    leg=leg,
                    unobservable_because="no meter was named for this subject, so the count "
                    "would have been over an empty predicate",
                )
            )
            continue
        values = meters if column == "meter_id" else [arguments.subject]
        placeholders = ", ".join("?" for _ in values)
        count, why = _athena_count(
            athena,
            arguments.workgroup,
            database,
            # The identifiers are this file's own constants; only the values come from outside,
            # and those are bound above.
            f"SELECT count(*) FROM {database}.{table} WHERE {column} IN ({placeholders})",  # noqa: S608
            values,
        )
        observations.append(Observation(leg=leg, rows=count, unobservable_because=why))

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
