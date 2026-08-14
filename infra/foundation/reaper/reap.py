"""Destroy anything whose `watermark:expires-at` has passed.

Runs hourly. It is the floor under the cost controls: the budget action stops more being
created, and this removes what already exists — because the expensive things in this estate
cost money for exactly as long as they exist, and nothing about a forgotten one looks wrong.

Two rules keep it from being the most dangerous thing in the account.

**It only ever sees resources tagged with this project.** The tag condition is on the IAM
policy, not here: a bug in this file cannot widen what the role may delete.

**A resource with no expiry is left alone, and reported.** Not deleted, and not silently
skipped either — an untagged resource is either a mistake in the Terraform or something created
by hand, and both are worth a line in the log rather than a deletion or a shrug.

**And it deletes, which for a long time it did not.** The first version classified every expired
resource, logged `would delete`, and returned the list. The mapping below named the API for each
type and nothing ever called one. That is the worst available shape for a control: the schedule
fired hourly, the log filled with lines that read like work, the return value was a list of ARNs,
and the resources billed for as long as anybody left them. `WATERMARK_REAPER_MODE` is what makes
the choice explicit rather than accidental — `destroy` is what the Terraform sets, `report` is
what the tests use, and there is no third state where it looks like one and behaves like the
other.
"""

from __future__ import annotations

import datetime as dt
import logging
import os

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

PROJECT = os.environ["WATERMARK_PROJECT"]
EXPIRY_TAG = "watermark:expires-at"

#: `arn:aws:service:region:account:type/name` — the field an ARN keeps its service in, and the
#: one it keeps the resource in. Named because this file splits ARNs in two places and a literal
#: index in each is two chances to pick the wrong one.
ARN_SERVICE = 2
ARN_RESOURCE = 5

#: Deletions this reaper knows how to perform, by resource-ARN service and type. A resource
#: type absent from here is reported rather than deleted — guessing an API from an ARN is how a
#: sweeper deletes a table when it meant to stop an application.
DELETERS = {
    ("kinesisanalyticsv2", "application"): "_delete_flink_application",
    ("sagemaker", "endpoint"): "_delete_endpoint",
    ("sagemaker", "feature-group"): "_delete_feature_group",
    ("kinesis", "stream"): "_delete_stream",
}

#: `destroy` deletes; anything else classifies and reports. The Terraform sets `destroy`, so the
#: default here is the safe one: a deployment that forgets the variable under-deletes, which
#: costs money, rather than over-deleting, which costs data.
MODE = os.environ.get("WATERMARK_REAPER_MODE", "report")


def _name_of(arn: str) -> str:
    """The resource's own name, which every delete API wants instead of the ARN."""
    parts = arn.split(":")
    tail = parts[ARN_RESOURCE] if len(parts) > ARN_RESOURCE else ""
    return tail.split("/", 1)[1] if "/" in tail else tail


def _delete_flink_application(arn: str, clients) -> None:
    """Stop before deleting, and pass the create timestamp the API insists on.

    A Managed Flink application cannot be deleted while it runs, and `DeleteApplication` takes
    the `CreateTimestamp` as an optimistic-concurrency token — so this is two describes and two
    calls rather than one, and skipping the stop is how a reaper reports success against an
    application that is still billing KPUs.
    """
    name = _name_of(arn)
    client = clients("kinesisanalyticsv2")
    detail = client.describe_application(ApplicationName=name)["ApplicationDetail"]
    if detail["ApplicationStatus"] in ("RUNNING", "STARTING", "UPDATING"):
        client.stop_application(ApplicationName=name, Force=True)
    client.delete_application(ApplicationName=name, CreateTimestamp=detail["CreateTimestamp"])


def _delete_endpoint(arn: str, clients) -> None:
    clients("sagemaker").delete_endpoint(EndpointName=_name_of(arn))


def _delete_feature_group(arn: str, clients) -> None:
    clients("sagemaker").delete_feature_group(FeatureGroupName=_name_of(arn))


def _delete_stream(arn: str, clients) -> None:
    clients("kinesis").delete_stream(StreamName=_name_of(arn), EnforceConsumerDeletion=True)


def handler(event: dict, context: object, clients=None) -> dict:
    """`clients` is injectable so the sweep is testable without an account.

    Not a nicety: every branch worth checking here is a branch that deletes something, and the
    only way to exercise those on a laptop is to hand the function something that records the
    call instead of making it. boto3 is resolved here rather than imported at module scope for
    the same reason — the Lambda runtime provides it and the machine running the tests does not.
    """
    if clients is None:
        import boto3  # noqa: PLC0415

        clients = boto3.client

    now = dt.datetime.now(dt.UTC)
    expired, deleted, failed, kept, unknown, untagged = [], [], [], [], [], []

    paginator = clients("resourcegroupstaggingapi").get_paginator("get_resources")
    pages = paginator.paginate(
        TagFilters=[{"Key": "watermark:project", "Values": [PROJECT]}],
    )

    for page in pages:
        for resource in page["ResourceTagMappingList"]:
            arn = resource["ResourceARN"]
            tags = {tag["Key"]: tag["Value"] for tag in resource["Tags"]}
            raw = tags.get(EXPIRY_TAG, "")

            if not raw or raw == "never":
                untagged.append(arn)
                continue
            try:
                expires = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                # An unparseable expiry is not an excuse to delete. It is a bug in whatever
                # wrote the tag, and deleting on it would make a typo destructive.
                untagged.append(arn)
                continue
            if expires > now:
                kept.append(arn)
                continue

            service, kind = _classify(arn)
            if (service, kind) not in DELETERS:
                unknown.append(arn)
                continue

            expired.append(arn)
            if MODE != "destroy":
                LOG.info("expired at %s, would delete: %s", raw, arn)
                continue
            try:
                globals()[DELETERS[(service, kind)]](arn, clients)
                deleted.append(arn)
                LOG.info("expired at %s, deleted: %s", raw, arn)
            except Exception:
                # One resource that will not delete must not stop the sweep. The rest are still
                # billing, and the dead-letter topic is what carries the failure out of here —
                # re-raising would abandon everything after this ARN in the page.
                LOG.exception("expired at %s and could not be deleted: %s", raw, arn)
                failed.append(arn)

    LOG.info(
        "reaper (%s): %d expired, %d deleted, %d failed, %d still live, "
        "%d expired but unknown type, %d without an expiry",
        MODE,
        len(expired),
        len(deleted),
        len(failed),
        len(kept),
        len(unknown),
        len(untagged),
    )
    if failed:
        # Raised *after* the sweep, so everything deletable has been deleted. This is what puts
        # the invocation on the dead-letter topic; a reaper that silently fails to delete is
        # indistinguishable from one that had nothing to do.
        raise RuntimeError(f"{len(failed)} expired resources could not be deleted: {failed}")

    return {
        "mode": MODE,
        "expired": expired,
        "deleted": deleted,
        "kept": len(kept),
        "unknown_type": unknown,
        "without_expiry": untagged,
    }


def _classify(arn: str) -> tuple[str, str]:
    """`arn:aws:service:region:account:type/name` into `(service, type)`."""
    parts = arn.split(":")
    service = parts[ARN_SERVICE] if len(parts) > ARN_SERVICE else ""
    tail = parts[ARN_RESOURCE] if len(parts) > ARN_RESOURCE else ""
    kind = tail.split("/")[0] if "/" in tail else tail
    return service, kind
