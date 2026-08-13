"""The seeded event stream, with every pathology in `docs/SCENARIO.md` present on purpose.

Deterministic without a random number generator at all. Consumption is a function of the meter
and the interval, arrival offsets are a function of the record — so there is no seed to get
wrong, no `random.Random` whose algorithm could change between Python releases, and no way for
two runs to differ. `scripts/seed_check.py` proves it by regenerating and comparing digests.

Every pathology is *labelled*: the cast names which meters duplicate, which are skewed past
tolerance, which the legacy head-end delivers late and which have deliberate gaps. A harness
can therefore assert an exact count. `docs/SCENARIO.md` ends on the reason: a synthetic dataset
with no pathology proves nothing — and one whose pathologies cannot be counted proves almost as
little, because every assertion about it has to be a range.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from data import cast
from watermark.core.records import METER_INTERVAL, Source
from watermark.core.time import Duration, Instant

#: A full day at fifteen minutes.
INTERVALS_PER_DAY: Final = 96

#: Meters upload in a burst after each interval boundary rather than evenly — the property
#: `docs/AWS-CONSTRAINTS.md` sizes the Kinesis shard count against.
BURST_WINDOW: Final = Duration.of_seconds(180)

#: The legacy head-end's file lands three days after the day it covers.
BATCH_DELAY: Final = Duration.of_days(3)

#: SUB-03's comms drop between 10:00 and 10:40. The meters keep metering and buffer; the
#: readings arrive in a burst afterwards. Nothing is lost — the partition simply says nothing
#: for forty minutes, which is claim 1's sharpest case and the one that fails silently.
IDLE_FROM: Final = Instant.from_iso(f"{cast.DAY}T10:00:00Z")
IDLE_UNTIL: Final = Instant.from_iso(f"{cast.DAY}T10:40:00Z")


@dataclass(frozen=True, slots=True)
class Delivery:
    """One raw payload as the platform receives it.

    The partition is carried beside the payload rather than parsed out of it: in the deployed
    system it is the Kinesis partition key, decided by the producer, and a consumer that had to
    parse a payload to know which partition it came from would be parsing before it could
    decide whether the payload is trustworthy.
    """

    raw: str
    ingest_time: Instant
    source: Source
    partition: str

    def sort_key(self) -> tuple[int, str, str]:
        return (self.ingest_time.epoch_millis, self.partition, self.raw)


def interval_start(index: int) -> Instant:
    return cast.DAY_START.plus(Duration.of_millis(index * METER_INTERVAL.millis))


def energy_wh(meter: cast.Meter, index: int) -> int:
    """A plausible interval consumption, derived rather than drawn.

    A diurnal shape — low overnight, peaks morning and evening — plus a per-meter offset, all
    from a hash so that it is stable across Python versions and machines. `random.Random` would
    also be reproducible today; a hash is reproducible in ten years, and a committed recording
    is a promise about ten years.
    """
    hour = (index * METER_INTERVAL.millis) // Duration.of_hours(1).millis
    # 0 at 04:00, peak around 08:00 and 19:00. Integer arithmetic throughout: energy is an
    # integer count of watt-hours everywhere in this system, and a float here would put a
    # rounding decision in the one place nobody would look for it.
    shape = 40 + abs(((hour + 4) % 12) - 6) * 12 + (60 if hour in (7, 8, 18, 19) else 0)
    jitter = int(hashlib.sha256(f"{meter.meter_id}|{index}".encode()).hexdigest()[:4], 16) % 25
    return shape + jitter


def payload(meter: cast.Meter, index: int, event_time: Instant) -> str:
    """The reading in the shape this meter's firmware emits, at its generated value."""
    return payload_with(meter, event_time, energy_wh(meter, index))


def _arrival_offset(meter: cast.Meter, index: int) -> Duration:
    """Where in the post-boundary burst this meter lands. Derived, so it is stable."""
    digest = int(hashlib.sha256(f"arrival|{meter.meter_id}|{index}".encode()).hexdigest()[:6], 16)
    return Duration.of_millis(digest % BURST_WINDOW.millis)


def _deliveries_for(meter: cast.Meter, index: int) -> list[Delivery]:
    if index in meter.missing_intervals:
        return []  # a deliberate evidence gap: this reading does not exist at all

    start = interval_start(index)
    event_time = start.plus(Duration.of_millis(meter.skew_ms))
    ingest = start.plus(METER_INTERVAL).plus(_arrival_offset(meter, index))

    # SUB-03's comms outage. The meter keeps metering; the upload waits until the link is back,
    # and then everything buffered arrives at once.
    if (
        meter.substation_id == cast.IDLE_SUBSTATION
        and IDLE_FROM.epoch_millis <= ingest.epoch_millis < IDLE_UNTIL.epoch_millis
    ):
        ingest = IDLE_UNTIL.plus(_arrival_offset(meter, index))

    raw = payload(meter, index, event_time)
    deliveries = [Delivery(raw, ingest, Source.STREAM, meter.substation_id)]

    # The retrying cohort: the identical payload again. Identical, so it collapses on content
    # rather than on a sequence number nobody has.
    #
    # Two shapes, alternating, and the second one exists because gate-proof found the gap. A
    # retry that always lands a minute and a half later is always in a different processing
    # batch from the original, so the two can never be reordered relative to each other — and a
    # deduplication rule that kept "whichever arrived first" stayed perfectly deterministic
    # under shuffling. Real at-least-once delivery also produces the near-simultaneous kind: a
    # retry that races the original into the same second. That is the one that can be reordered,
    # and therefore the one that tests the rule.
    if meter.duplicates:
        gap = Duration.of_seconds(90) if index % 2 == 0 else Duration.of_millis(200)
        deliveries.append(Delivery(raw, ingest.plus(gap), Source.STREAM, meter.substation_id))
    return deliveries


def _late_batch_for(meter: cast.Meter, index: int) -> list[Delivery]:
    """The legacy head-end's file, three days late, carrying a *different* value.

    This is the restatement case, and the difference matters: a late file that agreed with the
    stream would prove the pipeline can absorb late data, which is easy. A late file that
    disagrees is what forces a published total to move while the previous value survives —
    doctrine 4, and the whole reason the settlement path is in this project.

    The head-end reads the meter's register directly, so its value is the authoritative one and
    the stream's was a partial upload. The difference is deterministic and small, as a real
    register-versus-interval discrepancy is.
    """
    if index in meter.missing_intervals:
        return []
    # The same event time the device reported, skew and all. The head-end corrects the *value*,
    # not the attribution: a file that also moved the reading into a different interval would
    # restate two windows at once and make every delta a puzzle, which would hide the thing the
    # case exists to show. That a negatively-skewed device puts its own readings in the previous
    # interval is a real effect and it is exercised by the stream, on its own, where it can be
    # asserted about directly.
    event_time = interval_start(index).plus(Duration.of_millis(meter.skew_ms))
    corrected = energy_wh(meter, index) + 7 + (index % 3)
    raw = payload_with(meter, event_time, corrected)
    ingest = cast.DAY_END.plus(BATCH_DELAY).plus(Duration.of_seconds(index))
    return [Delivery(raw, ingest, Source.BATCH, meter.substation_id)]


def payload_with(meter: cast.Meter, event_time: Instant, watt_hours: int) -> str:
    """The reading in the shape this meter's firmware emits, at an explicit value.

    Three generations on the wire at once. fw2's kilowatt-hours are built as an exact decimal
    string rather than through a float: the generator must not be the place a value is already
    wrong before the normaliser has a chance to be right about it.
    """
    if meter.firmware == "fw1":
        return json.dumps(
            {
                "v": 1,
                "mid": meter.meter_id,
                "ts": event_time.epoch_millis // 1000,
                "wh": watt_hours,
            },
            separators=(",", ":"),
        )
    if meter.firmware == "fw2":
        kilowatt_hours = f"{watt_hours // 1000}.{watt_hours % 1000:03d}"
        return (
            f'{{"schema":"2","meter_id":"{meter.meter_id}",'
            f'"timestamp":"{event_time.to_iso()}",'
            f'"energy":{{"value":{kilowatt_hours},"unit":"kWh"}}}}'
        )
    return json.dumps(
        {
            "schemaVersion": "3.0",
            "meter": {"id": meter.meter_id},
            "interval": {"start": event_time.to_iso()},
            "energy": {"value": watt_hours, "unit": "Wh"},
        },
        separators=(",", ":"),
    )


def generate(intervals: int = INTERVALS_PER_DAY) -> tuple[Delivery, ...]:
    """Every delivery for the day, in canonical order.

    Sorted by content, not by the order the loops happened to build them. A generator whose
    output order depends on iteration order would make claim 2's shuffle test compare one
    accident against another.
    """
    deliveries: list[Delivery] = []
    for meter in cast.METERS:
        for index in range(intervals):
            deliveries.extend(_deliveries_for(meter, index))
            if meter.late_batch:
                deliveries.extend(_late_batch_for(meter, index))
    return tuple(sorted(deliveries, key=Delivery.sort_key))


def digest(deliveries: tuple[Delivery, ...]) -> str:
    """A hash of the whole stream, for the seed check.

    Over the canonical rendering of every field, so that a change to a payload, an arrival time,
    a source or a partition all move it. A digest over the payloads alone would let the arrival
    schedule drift silently, and the arrival schedule is half of what the lateness cases test.
    """
    material = "\n".join(
        f"{d.ingest_time.to_iso()}|{d.partition}|{d.source.value}|{d.raw}" for d in deliveries
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


#: Every shape's timestamp, as (pattern, how to read it, how to write it).
#:
#: The generator built these three shapes, so the generator is what rewrites them. It used to be
#: a regex in `data/publish.py` matching ISO-8601 instants, and the reasoning given was that a
#: timestamp is a timestamp at the transport layer. That is true of two shapes out of three:
#: **fw1 encodes its instant as epoch seconds**, an integer under `"ts"`, which no pattern for
#: ISO text will ever match.
#:
#: What that cost, live: a third of the fleet published with its event times untouched — months
#: before the capture window — so every window those meters opened was ancient, every correction
#: for one was more than the four-day allowance late, and all 164 were refused
#: `too_late_for_window`. The restatement case that carries doctrine 4 could not occur, and the
#: run looked healthy: the readings were published, the totals were right, and nothing anywhere
#: said that a third of the estate was living in March.
_TIMESTAMPS: Final = (
    # fw1: {"ts":1773445500}
    (
        re.compile(r'("ts":)(\d+)'),
        lambda m: Instant(int(m.group(2)) * 1000),
        lambda m, i: f"{m.group(1)}{i.epoch_millis // 1000}",
    ),
    # fw2: {"timestamp":"2026-03-14T10:00:00Z"} and fw3: {"start":"..."}
    #
    # `event_time` joins them for substation telemetry, and its absence was a real defect: the
    # telemetry payload is written by `data/telemetry.py` with the field the record class uses,
    # no pattern matched it, `retimed` returned it unchanged, and every measurement arrived in
    # the account stamped five months before the readings it was supposed to be decided
    # alongside. Nothing raised — a payload no pattern matches comes back untouched, deliberately,
    # so a firmware shape that grows a field is not a crash. The guard against that silence is
    # `tests/data/test_retimed.py`, which asserts that every shape this repository *publishes* is
    # one some pattern moves.
    (
        re.compile(r'("(?:timestamp|start|event_time)":")([^"]+)(")'),
        lambda m: Instant.from_iso(m.group(2)),
        lambda m, i: f"{m.group(1)}{i.to_iso()}{m.group(3)}",
    ),
)


def retimed(raw: str, move: Callable[[Instant], Instant]) -> str:
    """Rewrite a payload's event time through `move`, in whichever shape its firmware uses.

    Every shape is tried and every match is rewritten, so a payload that grows a second instant
    is moved consistently rather than half-moved. A payload no pattern matches comes back
    unchanged — and `tests/data/test_retimed.py` asserts that no shape in the cast is such a
    payload, which is the half that was missing.
    """
    for pattern, read, write in _TIMESTAMPS:
        # Bound as defaults rather than closed over. A lambda that captures the loop variables
        # would use whichever pair the loop finished on — every shape rewritten with the last
        # shape's reader, which for these two means an ISO parse of an integer.
        raw = pattern.sub(
            lambda match, read=read, write=write: write(match, move(read(match))), raw
        )
    return raw
