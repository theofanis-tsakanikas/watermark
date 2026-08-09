"""Collapsing the readings for one meter and one interval into the one that counts.

Meters retry. `docs/SCENARIO.md` puts a firmware cohort at roughly 2% duplication, and a
duplicate that survives into a settlement total is a customer billed twice for the same
electricity. The deduplication key is `(meter_id, interval_start, payload_hash)`, and the hash
covers the energy value — so a resent identical reading collapses, while a *different* value
for the same interval is a correction and must survive to become one (doctrine 4).

The part that is easy to get wrong, and that claim 2 exists to catch:

**"First one wins" is not deterministic under replay.** Two copies of one reading differ in
their ingestion time, and may differ in firmware and in whether they came off the stream or
out of a batch file. Whichever arrives first is an accident of partitioning, retry timing and
how the replay happened to be shuffled. Keep the first arrival and the same events in a
different order produce a different record — identical in energy, different in lineage — and
claim 2 is false in a way no total would reveal.

So this module does not filter a stream. It is a **reduction over the bag of readings for one
key**, with a total order that decides the winner from the records' own content. Shuffle the
input, duplicate it, deliver half of it three days late: the output is the same bytes.

That has a consequence worth stating out loud, because it shapes the whole pipeline: the
winner cannot be known until the bag is complete, and the bag is complete when the window
closes. Deduplication is therefore something a *closed window* does, not something the
ingestion path does on the way past. Which is claim 1, arrived at from the other direction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from watermark.core.records import MeterReading


@dataclass(frozen=True, slots=True)
class Collapsed:
    """The outcome of reducing one meter-interval's readings to one.

    Every count here is reported rather than logged. "How many duplicates did we suppress"
    and "how many corrections did we absorb" are the two questions asked of a metering
    pipeline in its first week, and a number that only exists in a log line is a number nobody
    can put in a settlement report.
    """

    #: The reading that counts. `None` only for an empty bag, which a closed window with no
    #: readings produces — an absence that is itself a fact, and one claim 1 cares about.
    winner: MeterReading | None
    #: Retries: readings identical in content to another, suppressed.
    duplicates_suppressed: int
    #: Distinct values seen for this interval. More than one means a correction arrived, and
    #: the losers are kept so that the restatement record can state what was superseded.
    superseded: tuple[MeterReading, ...]

    @property
    def was_corrected(self) -> bool:
        return bool(self.superseded)


def _retry_order(reading: MeterReading) -> tuple[int, int, str, str]:
    """A total order over identical readings, used to pick which copy represents them.

    Earliest ingestion first: the copy that arrived soonest is the one whose lineage best
    describes when the system could have acted on it. Everything after `ingest_time` is a
    tie-break, present only so that the order is *total* — two copies ingested in the same
    millisecond must still have a defined winner, or the shuffle test fails intermittently,
    which is the worst way for it to fail.
    """
    return (
        reading.ingest_time.epoch_millis,
        reading.event_time.epoch_millis,
        reading.firmware,
        reading.source.value,
    )


def _correction_order(reading: MeterReading) -> tuple[int, str]:
    """A total order over *differing* values for one interval: the latest correction wins.

    A meter that reports 312 Wh and later 340 Wh for the same interval is correcting itself,
    and the correction is the newer statement. Ties break on the payload hash — content, not
    arrival — so that two corrections ingested in the same millisecond resolve the same way in
    every replay.
    """
    return (reading.ingest_time.epoch_millis, reading.payload_hash)


def collapse(readings: Iterable[MeterReading]) -> Collapsed:
    """Reduce every reading for one meter and one interval to the single one that counts.

    Order-independent by construction: the result depends on the *set* of readings and on the
    orders defined above, never on the sequence they were supplied in.

    The caller is responsible for the bag holding one key. Mixing meters or intervals here
    would silently produce one answer for many, which is why `group` exists below rather than
    this function grouping defensively and hiding the mistake.
    """
    bag = list(readings)
    if not bag:
        return Collapsed(None, 0, ())

    # Retries first: within each distinct value, one representative.
    by_content: dict[str, list[MeterReading]] = {}
    for reading in bag:
        by_content.setdefault(reading.payload_hash, []).append(reading)

    representatives = [min(copies, key=_retry_order) for copies in by_content.values()]
    duplicates_suppressed = len(bag) - len(representatives)

    # Then corrections: across distinct values, the latest statement.
    ordered = sorted(representatives, key=_correction_order)
    winner = ordered[-1]
    superseded = tuple(ordered[:-1])

    return Collapsed(winner, duplicates_suppressed, superseded)


def group(readings: Iterable[MeterReading]) -> dict[tuple[str, int], list[MeterReading]]:
    """Bucket readings by `(meter_id, interval_start)`.

    The key uses the interval's epoch milliseconds rather than the `Instant` so that the
    grouping is a plain, hashable, orderable tuple — the shape a Flink `keyBy` and a golden
    recording can both hold without either needing to know about our types.
    """
    buckets: dict[tuple[str, int], list[MeterReading]] = {}
    for reading in readings:
        buckets.setdefault((reading.meter_id, reading.interval_start.epoch_millis), []).append(
            reading
        )
    return buckets
