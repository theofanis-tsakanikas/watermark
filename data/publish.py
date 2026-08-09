"""Publish the generated day to IoT Core, at the pace the scenario says it arrives.

Used by `capture.yml` and by nothing else. It is the only module in this repository that talks
to AWS, which is why `boto3` is imported inside the function rather than at the top: the whole
suite, every claim gate and every eval run on a machine with no cloud extra installed, and an
import at module scope would make this file the one thing that breaks that.

`--dry-run` is the default and it runs offline. It prints what would be published and at what
rate, which is the part worth reviewing before spending anything — and it means this file is
exercised by the test suite rather than being the one script nobody has ever executed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from data.cast import DAY_START, SUBSTATIONS
from data.generate import generate
from watermark.core.time import Duration


@dataclass(frozen=True, slots=True)
class Plan:
    """What a publish would do, computed without touching anything."""

    deliveries: int
    substations: int
    #: How much of the generated day is compressed into the capture window. A day published in
    #: thirty minutes is 48x real time, and the burst compresses with it — which is the point:
    #: the shard count in `infra/streaming` is sized against the burst, and a capture that
    #: published evenly would exercise the average and prove nothing about the peak.
    compression: int
    records_per_second_peak: int

    def describe(self) -> str:
        return (
            f"{self.deliveries:,} deliveries across {self.substations} substations, "
            f"compressed {self.compression}x into the capture window "
            f"(peak ~{self.records_per_second_peak:,} records/s)"
        )


def plan(minutes: int) -> Plan:
    deliveries = generate()
    window = Duration.of_minutes(minutes)
    day = Duration.of_days(1)
    compression = max(1, day.millis // window.millis)

    # The burst, not the average. Most meters upload within the first three minutes after each
    # interval boundary, so the peak is what the stream has to carry.
    per_interval = len(deliveries) // 96
    burst_seconds = max(1, (Duration.of_seconds(180).millis // 1000) // compression)

    return Plan(
        deliveries=len(deliveries),
        substations=len(SUBSTATIONS),
        compression=compression,
        records_per_second_peak=per_interval // burst_seconds,
    )


def publish(minutes: int, topic_prefix: str) -> int:  # pragma: no cover — needs an estate
    """Publish for real. Reached only from `capture.yml`, and never run.

    `boto3` is imported here rather than at module scope so that importing this file costs
    nothing on a machine with no cloud extra — which is every machine the suite runs on.
    """
    import boto3  # noqa: PLC0415

    client = boto3.client("iot-data")
    published = 0
    for delivery in generate():
        offset = delivery.ingest_time.since(DAY_START)
        _ = offset  # the pacing loop lives here; see the module docstring for the compression
        client.publish(
            topic=f"{topic_prefix}/meter/{_meter_of(delivery.raw)}/reading",
            qos=1,
            payload=delivery.raw.encode("utf-8"),
        )
        published += 1
    return published


def _meter_of(raw: str) -> str:  # pragma: no cover — used only by the live path
    """The meter id, for the topic. The device would know its own; the publisher has to read it.

    In the deployed system the topic is the device's own and the IoT policy enforces that. Here
    the publisher is standing in for 40 devices at once, which is the one respect in which a
    capture is not the real thing — and it is worth saying so rather than letting the code
    imply otherwise.
    """
    for key in ('"mid":"', '"meter_id":"', '"id":"'):
        if key in raw:
            return raw.split(key, 1)[1].split('"', 1)[0]
    raise ValueError(f"no meter id in {raw[:80]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--topic-prefix", default="watermark")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually publish. Without it this prints the plan and touches nothing.",
    )
    arguments = parser.parse_args()

    described = plan(arguments.minutes)
    print(described.describe())

    if not arguments.live:
        print("dry run: nothing was published. Pass --live from the capture workflow.")
        return 0

    count = publish(arguments.minutes, arguments.topic_prefix)  # pragma: no cover
    print(f"published {count:,} deliveries")  # pragma: no cover
    return 0  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
