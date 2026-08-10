#!/usr/bin/env python3
"""Everything reached from inside the VPC has a way out of it.

Egress is the endpoint list and nothing else — `infra/foundation` provisions no NAT gateway, on
purpose. The consequence is that **a service with no endpoint does not fail. It waits.** The SDK
retries, the socket eventually times out, and the control plane reports the application healthy
throughout. That failure has cost real time in the sibling project twice, which is why it is a
check rather than a habit.

The comparison is between the endpoints `infra/foundation` creates and the AWS services the
other layers' IAM policies grant actions against — because an action granted is an action
something intends to call, and something intending to call a service with no endpoint is the
whole failure mode.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: IAM service prefix → the VPC endpoint service name, where they differ. Kinesis data streams
#: are `kinesis:` in a policy and `kinesis-streams` as an endpoint, which is exactly the kind of
#: mismatch that makes this check worth automating.
ENDPOINT_NAMES = {
    "kinesis": "kinesis-streams",
    "firehose": "kinesis-firehose",
    "sagemaker": "sagemaker.api",
    "states": "states",
    "cloudwatch": "monitoring",
    # ECR is two endpoints and one IAM prefix. `ecr.api` serves the control plane — the
    # authorisation token, the manifest — and `ecr.dkr` serves the layers themselves. A policy
    # granting `ecr:` needs both, and naming only the first here would let a build reach the
    # manifest and hang pulling the layer, which is the failure shape this check exists for.
    "ecr": "ecr.api",
    "xray": "xray",
}

#: Services reached over the *control plane only*, from outside the VPC, by callers that are not
#: in it. Granting `iam:PassRole` does not mean anything inside a subnet calls IAM.
NOT_REACHED_FROM_THE_VPC = {
    "iam",
    "s3",  # a gateway endpoint, not an interface one; provisioned separately
    "dynamodb",  # likewise
    "athena",  # reached by Step Functions and by humans, both outside the VPC
    "budgets",
    "tag",
    "events",
    "sns",
    "lambda",
    "kinesisanalyticsv2",
    "ec2",
    # Devices reach IoT Core from the public internet over MQTT with their own X.509
    # certificate. Nothing inside a subnet calls it — the traffic goes the other way.
    "iot",
}


def _provisioned() -> set[str]:
    network = (ROOT / "infra" / "foundation" / "network.tf").read_text(encoding="utf-8")
    block = re.search(r"interface_endpoints\s*=\s*toset\(\[(.*?)\]\)", network, re.S)
    if not block:
        return set()
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def _granted() -> set[str]:
    services: set[str] = set()
    for path in ROOT.glob("infra/*/*.tf"):
        if path.parent.name in {"bootstrap", "foundation"}:
            continue
        # The action half must start with a capital or a star. Without that, every tag key in
        # the file — `watermark:project`, `watermark:expires-at` — is read as an IAM action
        # against a service called `watermark`, and the check reports a missing endpoint for a
        # service that does not exist.
        for service in re.findall(
            r'"([a-z0-9-]+):[A-Z*][A-Za-z0-9*]*"', path.read_text(encoding="utf-8")
        ):
            services.add(service)
    return services


def main() -> int:
    provisioned = _provisioned()
    needed = {
        ENDPOINT_NAMES.get(service, service)
        for service in _granted()
        if service not in NOT_REACHED_FROM_THE_VPC
    }

    missing = sorted(needed - provisioned)
    if missing:
        print("vpc-endpoints: a service is granted with no way out of the VPC\n", file=sys.stderr)
        for service in missing:
            print(
                f"  {service}: an IAM policy grants actions against it and "
                "infra/foundation provisions no endpoint. The call will not be refused — it "
                "will hang until the SDK gives up, while the control plane reports healthy.",
                file=sys.stderr,
            )
        print(
            "\nAdd it to `interface_endpoints` in infra/foundation/network.tf, or to "
            "NOT_REACHED_FROM_THE_VPC here if nothing inside a subnet actually calls it — "
            "with a reason.",
            file=sys.stderr,
        )
        return 1

    print(f"vpc-endpoints: {len(provisioned)} endpoints cover every service granted from inside")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
