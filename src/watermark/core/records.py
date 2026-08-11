"""The normalised records everything downstream reads.

One shape per stream, reached from every firmware variant, and nothing past `normalise.py`
ever sees a raw payload. That is the point of normalising early: a windowing function that has
to know which firmware wrote a record is a windowing function with three code paths and two
untested ones.

Two decisions are worth stating because they propagate everywhere.

**Energy is an integer count of watt-hours.** Not a float of kilowatt-hours. ADR-0004 forbids a
tolerance in the train/serve parity comparison, and a tolerance is the only way to compare
floats that travelled through two engines; the answer there was a scaled integer, and this is
where that scale is fixed. It also makes settlement exact: a sum of integers is the same number
in every order, and a sum of floats is not, which would end claim 2 on its own.

**Event time and ingestion time are both carried, always.** Event time says what the reading is
about; ingestion time says when the system could first have known it. Every hard question in
this project is a question about the gap between them — lateness, clock skew, restatement, and
the bitemporal parity comparison in ADR-0004. A record that carries only one of them cannot
answer any of those, and by the time anybody notices, the other has been lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from watermark.core.time import Duration, Instant

#: The metering interval. Named once, here, because a literal `Duration.of_minutes(15)` written
#: in three modules is three chances to write 5 — and a window boundary that disagrees between
#: the aggregator and the settlement query is a class of bug with no symptom except a total
#: that is wrong by one interval.
METER_INTERVAL = Duration.of_minutes(15)

#: The settlement grain. Hourly totals per meter and per balancing group.
SETTLEMENT_GRAIN = Duration.of_hours(1)

#: How often the pipeline stops accumulating and asks whether anything may close.
#:
#: A semantic decision, not an implementation detail, which is why it lives here rather than in
#: the adapter. It bounds how late a decision can be for a reason unrelated to data: a window
#: whose watermark passed halfway through a batch waits until the batch ends. One second is the
#: grain the deployed job checkpoints at, so the two cannot disagree about how much work a
#: restart repeats.
BATCH_GRAIN = Duration.of_seconds(1)

#: How long a closed window may wait before it is durable.
#:
#: The streaming job writes closed windows to a landing prefix and a Glue job merges them into
#: the silver table, so this bounds the gap between *deciding* and *being able to read the
#: decision*. That is a semantic budget, not a file-system tuning knob: settlement reads the
#: table, and a window that has closed but is not yet readable is invisible to every query that
#: would total it.
#:
#: A minute, because the only decision in this system that needs seconds — curtailment — never
#: reads the lakehouse, and settlement's horizon is days. It lives here rather than in the
#: adapter for the same reason `BATCH_GRAIN` does: a duration written into a connector call is
#: an answer moved where no offline test can read it.
LANDING_ROLLOVER = Duration.of_minutes(1)

#: And how long a part file may sit with nothing arriving before it is closed anyway.
#:
#: Without it a quiet meter's file never rolls, the merge finds nothing, and the stream looks
#: healthy while the table stays empty — the failure shape this project keeps meeting.
LANDING_IDLE = Duration.of_seconds(30)

#: The size at which a part file rolls regardless of time. 64 MiB is a compaction-friendly
#: floor: smaller parts mean more files for `compaction` to merge, which ADR-0002 accepted as
#: operational work but did not invite.
LANDING_PART_BYTES = 64 * 1024 * 1024


class Source(Enum):
    """How a reading reached us.

    Not cosmetic. A batch drop from the legacy head-end arrives up to three days late by
    design, so lateness that would be alarming on the stream is expected here — and the
    restatement records need to say which path caused a published total to move.
    """

    #: MQTT over IoT Core, from the meter itself.
    STREAM = "stream"
    #: An S3 file from the legacy AMI head-end, up to three days behind.
    BATCH = "batch"


@dataclass(frozen=True, slots=True)
class MeterReading:
    """One meter's energy over one 15-minute interval, normalised.

    Frozen because it travels through deduplication, windowing and settlement, and a record
    that can be edited in flight is a record whose payload hash stops describing it.
    """

    meter_id: str
    #: The start of the 15-minute interval this reading measures. Derived by flooring the
    #: device's event time, so two readings for the same interval collide in deduplication
    #: even when their timestamps differ by milliseconds.
    interval_start: Instant
    #: What the device said the time was. Kept alongside `interval_start` rather than replaced
    #: by it: the difference between the two is the evidence of clock skew, and flooring
    #: destroys it.
    event_time: Instant
    #: When the platform first saw the record. Minted at the edge and passed in — never read
    #: from a clock inside the core, which `scripts/check_core_is_pure.py` enforces.
    ingest_time: Instant
    #: Interval energy in watt-hours. Integer; see the module docstring.
    energy_wh: int
    firmware: str
    source: Source
    #: A hash of the normalised content. Part of the deduplication key, so that a retry of the
    #: identical reading collapses while a *corrected* reading for the same interval does not
    #: — the second is a restatement and must survive to be one.
    payload_hash: str

    @property
    def interval_end(self) -> Instant:
        return self.interval_start.plus(METER_INTERVAL)

    @property
    def lateness(self) -> Duration:
        """How far behind its interval's end the record arrived.

        Negative for a record that arrived before its interval had even finished, which is
        normal for a meter reporting mid-interval and is not an error.
        """
        return self.ingest_time.since(self.interval_end)

    @property
    def skew(self) -> Duration:
        """How far ahead of ingestion the device's clock claims to be.

        Positive means the device thinks it is in the future relative to when we received the
        record, which is only possible if its clock is wrong.
        """
        return self.event_time.since(self.ingest_time)


@dataclass(frozen=True, slots=True)
class SubstationTelemetry:
    """A substation's measured load, once per second.

    The safety-relevant signal: this is what the curtailment fallback rule is computed from
    when no forecast is available, so it must be usable with no model and no feature store.
    """

    substation_id: str
    event_time: Instant
    ingest_time: Instant
    #: Measured load in watts. Integer, for the same reason as `energy_wh`.
    load_w: int
    #: The substation's declared thermal limit at this instant, resolved point-in-time from the
    #: SCD-2 reference data — limits change seasonally. Carried on the record rather than
    #: looked up later so that a decision and the limit it was taken against cannot drift
    #: apart in the record.
    limit_w: int

    @property
    def headroom_w(self) -> int:
        """Watts remaining before the declared limit. Negative when already over it."""
        return self.limit_w - self.load_w

    @property
    def utilisation_basis_points(self) -> int:
        """Load as a fraction of the limit, in basis points.

        Basis points rather than a float percentage, so the value can appear in a decision
        record, a threshold comparison and a published total without ever being a float that
        two engines round differently. A limit of zero would be a substation with no capacity;
        it is treated as fully utilised rather than raising, because a division error inside a
        safety path is the worst possible place for one.
        """
        if self.limit_w <= 0:
            return 10_000
        return self.load_w * 10_000 // self.limit_w


@dataclass(frozen=True, slots=True)
class ChargeSessionTick:
    """One second of an EV charging session.

    Session start and stop can arrive out of order relative to the ticks, which is why a tick
    carries everything needed to attribute it — the session, the charger and the substation —
    rather than relying on a start record having been seen first.
    """

    session_id: str
    charger_id: str
    substation_id: str
    event_time: Instant
    ingest_time: Instant
    #: Instantaneous power draw in watts.
    power_w: int
    #: The ceiling currently in force for this session, in watts. A curtailment decision moves
    #: this; recording it on the tick is what makes it possible to show afterwards that a
    #: throttle was actually applied rather than merely emitted.
    limit_w: int
