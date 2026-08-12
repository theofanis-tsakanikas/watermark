#!/usr/bin/env python3
"""The transport's `partition` and the core's declared partitions are the same vocabulary.

`watermark.core.watermarks` declares its partitions up front — a substation that has never
spoken must still hold the watermark back, so the set cannot be discovered from traffic. The
declared set comes from `data.cast.SUBSTATIONS` by way of `WATERMARK_PARTITIONS`. What arrives
beside each record is whatever the IoT topic rule put in the field called `partition`.

Nothing made those the same thing, and for the whole of phase 4 they were not. The rule read
`topic(3)` from `<project>/meter/<thing>/reading`, which is the **meter id**, so every record
named a partition the core had never heard of while all four declared substations lagged
infinitely and were excluded as idle. Live, on every published row:

    "holding_back": "M00038",
    "idle_partitions": ["SUB-01", "SUB-02", "SUB-03", "SUB-04"]

Every total was published in `advancing_with_idle` carrying a hole that did not exist, and claim
1's sharpest case — SUB-03 going quiet for forty minutes — could not fire, because SUB-03 never
spoke at all.

**Neither `terraform validate` nor any claim harness can see this.** The rule is valid SQL, the
core is correct, the eval suite drives the core directly with the right partitions, and the two
only meet in a running estate. So it is checked here, on the one thing both sides commit to
writing down: the topic.

Three statements are compared:

  * the topic filter and the `topic(N) AS partition` projection in `infra/streaming/iot.tf`,
  * the Kinesis `partition_key`, which must be the *meter* — high cardinality, for shards,
  * the topic `data/publish.py` publishes to, segment by segment.

MQTT topic segments are one-indexed, the way `topic()` counts them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: What the publisher must interpolate into the segment the rule calls `partition`. It is the
#: substation the generator assigned the meter to — the comms domain that goes silent, which is
#: the only thing a watermark partition can usefully be.
PARTITION_EXPRESSION = "delivery.partition"

#: And into the segment used as the Kinesis partition key. A shard key wants cardinality; a
#: watermark partition wants the physical grouping. They are different answers and the bug was
#: that one field had to be both.
SHARD_KEY_EXPRESSION = "_meter_of("


def _rule_topics() -> list[tuple[str, int, int]]:
    """Every rule, as (topic filter, partition segment, shard-key segment)."""
    text = (ROOT / "infra" / "streaming" / "iot.tf").read_text(encoding="utf-8")
    rules = []
    for chunk in text.split('resource "aws_iot_topic_rule"')[1:]:
        sql = re.search(r'sql\s*=\s*"(.*?)"\n', chunk)
        key = re.search(r"partition_key\s*=\s*\"\$\$\{topic\((\d+)\)\}\"", chunk)
        if not sql or not key:
            continue
        statement = sql.group(1)
        partition = re.search(r"topic\((\d+)\)\s+AS\s+partition", statement)
        topic = re.search(r"FROM\s+'([^']+)'", statement)
        if not partition or not topic:
            continue
        rules.append((topic.group(1), int(partition.group(1)), int(key.group(1))))
    return rules


def _published_topic() -> list[str]:
    """The publisher's topic template, split into segments.

    Read out of the f-string rather than by importing and calling: the publish path needs boto3
    and an estate, and a check that cannot run offline is a check that does not run.
    """
    text = (ROOT / "data" / "publish.py").read_text(encoding="utf-8")
    match = re.search(r'topic=f"([^"]+)"', text)
    if not match:
        raise SystemExit("check-partition-vocabulary: no topic= f-string found in data/publish.py")
    return match.group(1).split("/")


def main() -> int:
    problems = []
    published = _published_topic()
    rules = _rule_topics()

    if not rules:
        problems.append(
            "no IoT topic rule with both a `topic(N) AS partition` projection and a "
            "`partition_key` was found. That is either a rule that does not label its records "
            "or a pattern this check no longer understands; both need a person."
        )

    for topic, partition_segment, shard_segment in rules:
        segments = topic.split("/")

        if len(segments) != len(published):
            problems.append(
                f"the rule subscribes to `{topic}` ({len(segments)} segments) and the publisher "
                f"publishes to `{'/'.join(published)}` ({len(published)} segments). A topic that "
                "does not match is a rule that never fires, and a stream that stays empty while "
                "every component reports healthy."
            )
            continue

        # One-indexed, the way `topic()` counts.
        claimed = published[partition_segment - 1]
        if PARTITION_EXPRESSION not in claimed:
            problems.append(
                f"the rule reads segment {partition_segment} of `{topic}` as `partition`, and "
                f"the publisher puts `{claimed}` there rather than `{PARTITION_EXPRESSION}`. The "
                "core declares its partitions from the substation list, so a record arriving "
                "under any other name is a partition it has never heard of — and every declared "
                "substation then lags for ever and is excluded as idle."
            )

        shard = published[shard_segment - 1]
        if SHARD_KEY_EXPRESSION not in shard:
            problems.append(
                f"the Kinesis partition key is segment {shard_segment} of `{topic}`, where the "
                f"publisher puts `{shard}`. That key decides shard distribution and wants the "
                "meter: keying on the substation puts a quarter of the fleet on one shard."
            )

        if partition_segment == shard_segment:
            problems.append(
                f"the watermark partition and the Kinesis partition key are both segment "
                f"{partition_segment}. They are different questions — which comms domain a "
                "record came from, and which shard it should land on — and one field cannot be "
                "the right answer to both."
            )

    if problems:
        print("check-partition-vocabulary: the transport and the core disagree\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"check-partition-vocabulary: {len(rules)} IoT rules label records with the same "
        "vocabulary the core declares"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
