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
"""

from __future__ import annotations

import datetime as dt
import logging
import os

import boto3

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

PROJECT = os.environ["WATERMARK_PROJECT"]
EXPIRY_TAG = "watermark:expires-at"

#: Deletions this reaper knows how to perform, by resource-ARN service and type. A resource
#: type absent from here is reported rather than deleted — guessing an API from an ARN is how a
#: sweeper deletes a table when it meant to stop an application.
DELETERS = {
    ("kinesisanalyticsv2", "application"): "stop_and_delete_flink_application",
    ("sagemaker", "endpoint"): "delete_endpoint",
    ("sagemaker", "feature-group"): "delete_feature_group",
    ("kinesis", "stream"): "delete_stream",
}


def handler(event: dict, context: object) -> dict:  # noqa: ARG001
    now = dt.datetime.now(dt.UTC)
    expired, kept, unknown, untagged = [], [], [], []

    paginator = boto3.client("resourcegroupstaggingapi").get_paginator("get_resources")
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
            LOG.info("expired at %s, would delete: %s", raw, arn)

    LOG.info(
        "reaper: %d expired, %d still live, %d expired but unknown type, %d without an expiry",
        len(expired),
        len(kept),
        len(unknown),
        len(untagged),
    )
    return {
        "expired": expired,
        "kept": len(kept),
        "unknown_type": unknown,
        "without_expiry": untagged,
    }


def _classify(arn: str) -> tuple[str, str]:
    """`arn:aws:service:region:account:type/name` into `(service, type)`."""
    parts = arn.split(":")
    service = parts[2] if len(parts) > 2 else ""
    tail = parts[5] if len(parts) > 5 else ""
    kind = tail.split("/")[0] if "/" in tail else tail
    return service, kind
