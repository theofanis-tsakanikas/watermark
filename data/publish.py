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
import re
import sys
from dataclasses import dataclass

from data.cast import SUBSTATIONS
from data.generate import generate
from watermark.core.records import Source
from watermark.core.time import Duration, Instant


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

    # The span the deliveries actually cover, not an assumed day.
    #
    # A day was the obvious number and it was wrong: the head-end's file is **three days late**,
    # so the generated set spans four days, and compressing it as one put every one of its 288
    # corrections at minute eighty of a twenty-minute window. The publisher stopped at twenty,
    # the corrections were never sent, and **no restatement was ever produced in a live run** —
    # the edge case carrying doctrine 4, silently absent because of an arithmetic assumption.
    #
    # `max(...) - min(...)` over the real ingest times cannot make that mistake again, and it is
    # correct whatever the generator's shape becomes.
    arrivals = [delivery.ingest_time.epoch_millis for delivery in deliveries]
    span = max(max(arrivals) - min(arrivals), 1)
    compression = max(1, span // window.millis)

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


#: Every ISO-8601 instant in a payload, whatever firmware shape holds it.
#:
#: Textual rather than schema-aware on purpose. The three firmware shapes put the instant under
#: three different keys, and a publisher that had to know which is a publisher doing the core's
#: job — `normalise` owns that question. A timestamp is a timestamp at the transport layer.
_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


def _compress_instants(raw: str, origin: int, start: int, compression: int) -> str:
    """Map every instant in the payload through the same transform as the pacing.

    **This is what makes late data late by the right amount.** The publisher already compressed
    *arrival* times into the capture window and left *event* times untouched, so a correction
    that was three days late at source arrived three days late against a window that had closed
    seconds earlier — 150 days past its interval, in a run that lasted six minutes. The core
    refused all 288 of them for exactly the reason it should, and doctrine 4 could never be
    demonstrated live.

    Compressing both keeps the *relationship* between event time and arrival, which is the only
    thing the scenario is about. A reading three days late becomes seconds late; a window that
    closes and is then corrected still closes and is then corrected.
    """

    def shift(match: re.Match[str]) -> str:
        instant = Instant.from_iso(match.group(0))
        moved = start + (instant.epoch_millis - origin) // compression
        return Instant(moved).to_iso()

    return _INSTANT.sub(shift, raw)


def publish(minutes: int, topic_prefix: str) -> int:  # pragma: no cover — needs an estate
    """Publish for real, at the compressed pace the plan describes.

    Reached only from `capture.yml`. `boto3` and `time` are imported here rather
    than at module scope so that importing this file costs nothing on a machine with no cloud
    extra — which is every machine the suite runs on, and `time` is a clock the rest of this
    repository is careful not to have.

    **It paces.** An earlier version computed the offset and discarded it, which would have
    published the whole day as fast as the API allowed: the burst that `infra/streaming`'s shard
    count is sized against would have arrived as one flat wall, the capture would have shown
    throttling that the design does not have, and the number it produced would have been about
    the publisher rather than about the platform.
    """
    import time  # noqa: PLC0415

    import boto3  # noqa: PLC0415

    client = boto3.client("iot-data")
    described = plan(minutes)
    deliveries = generate()

    # Paced from the first arrival, not from midnight. `DAY_START` is a fixed instant and the
    # generated set does not begin on it, so every offset carried a constant shift — enough to
    # put the last corrections a fraction past the end of the window and drop them.
    origin = min(delivery.ingest_time.epoch_millis for delivery in deliveries)

    # Where the compressed day lands on the wall clock. The core measures lateness in real
    # elapsed time, so a scenario replayed months after its seeded date is a scenario in which
    # everything is months late. Anchoring it to now is what makes the run a replay rather than
    # an archive.
    wall_start = int(time.time() * 1000)

    started = time.monotonic()
    published = 0

    for delivery in deliveries:
        # Where this delivery sits in the run, compressed into the capture window. Sleeping
        # until then is what reproduces the burst shape rather than the daily average.
        due = (delivery.ingest_time.epoch_millis - origin) / 1000 / described.compression
        behind = due - (time.monotonic() - started)
        if behind > 0:
            time.sleep(behind)

        # Routed by what the delivery *is*. The head-end's late file is a correction, not a
        # slow reading, and the two arrive on different topics so the rules can label them
        # differently — `stream` and `batch`. Publishing both to `/reading` made every
        # correction claim to be live, so the core refused all 288 as past their window and no
        # restatement was ever produced.
        leaf = "reading" if delivery.source is Source.STREAM else "backfill"
        payload = _compress_instants(delivery.raw, origin, wall_start, described.compression)
        client.publish(
            topic=f"{topic_prefix}/meter/{_meter_of(delivery.raw)}/{leaf}",
            qos=1,
            payload=payload.encode("utf-8"),
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
