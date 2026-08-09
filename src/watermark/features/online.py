"""The online mechanism: an incremental aggregator the stream advances, record by record.

Deliberately unlike `offline.py`. That one is a set-oriented query, recomputed whole, stateless.
This one holds state per entity, sees each record once, and never looks at the window again —
which is what a streaming materialisation actually is, and why agreement between the two is
evidence rather than a tautology (ADR-0004).

The differences are not cosmetic. A `mean` here is a running sum and count; there it is an
aggregate over rows. A `min` here can never rise when a low reading falls out of the window,
because the record is gone; there it recomputes. **Those are real ways the two can disagree**,
and they are precisely what claim 3 is for. Papering over them with a shared helper would
remove the disagreement and the evidence together.

The eviction rule is what keeps them honest: this aggregator holds the records still inside the
window, so that a value falling out of it changes the answer here the same way it does there. A
running sum with no eviction would be cheaper, would drift, and would drift *slowly* — the
worst available failure, because it is right at first.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from watermark.contracts.features import FeatureContract
from watermark.core.time import Duration, Instant


@dataclass(frozen=True, slots=True)
class ServedValue:
    """What the online store returns, and everything a decision needs to judge it.

    The value alone is not enough. Claim 4 turns on *how old* it is, and a serving path that
    returned a bare number would force every caller to fetch the event time separately and
    remember to compare it — which one of them eventually would not.
    """

    entity_id: str
    feature_id: str
    value: int
    #: The event time of the newest record in the value. This is what freshness is measured
    #: from, not the moment the record was written.
    event_time: Instant
    #: The Feature Store's own write time, for the bitemporal parity comparison.
    write_time: Instant

    def age_at(self, moment: Instant) -> Duration:
        return moment.since(self.event_time)

    def is_fresh_at(self, moment: Instant, budget: Duration) -> bool:
        return self.age_at(moment).millis <= budget.millis

    def to_feature_store_event_time(self) -> str:
        """The event time in the one shape SageMaker Feature Store accepts for Iceberg groups.

        `yyyy-MM-dd'T'HH:mm:ss.SSSSSSSSSZ` — nine fractional digits. This repository's canonical
        instant renders three, which matches neither accepted pattern, so the widening happens
        here and the core stays as it is: three decimals is what Flink carries, and a core that
        rendered nanoseconds would claim a precision the runtime does not have.
        """
        base = self.event_time.to_iso().removesuffix("Z")
        return f"{base}000000Z"


@dataclass(slots=True)
class OnlineMaterialiser:
    """Per-entity incremental state for one feature.

    Mutable, like the Flink keyed state it maps onto. What matters for claim 2 is determinism,
    not immutability: every transition depends only on the records and the contract.
    """

    contract: FeatureContract
    #: Records still inside the window, oldest first. A deque because eviction is from the left
    #: and admission is from the right, which is the whole of the access pattern.
    _windows: dict[str, deque[tuple[Instant, int]]] = field(default_factory=dict, init=False)
    _write_times: dict[str, Instant] = field(default_factory=dict, init=False)

    def observe(self, entity_id: str, event_time: Instant, value: int, write_time: Instant) -> None:
        """Take one record into the entity's window and evict whatever has fallen out."""
        window = self._windows.setdefault(entity_id, deque())
        window.append((event_time, value))

        # Insertion order is not event-time order: records arrive out of order. Sorting on
        # every observation is what keeps eviction correct — and it is also, honestly, the
        # place this mechanism is slower than a real one would be. A production materialiser
        # would hold a heap; the shape of the answer is the same.
        window_records = sorted(window, key=lambda record: record[0].epoch_millis)
        cutoff = event_time.minus(self.contract.window.length)
        self._windows[entity_id] = deque(
            record for record in window_records if record[0].epoch_millis > cutoff.epoch_millis
        )

        held = self._write_times.get(entity_id)
        if held is None or write_time.epoch_millis > held.epoch_millis:
            self._write_times[entity_id] = write_time

    def serve(self, entity_id: str) -> ServedValue | None:
        """What `GetRecord` would return. `None` when this entity has no value at all.

        The online store keeps only the record with the latest event time, so serving returns
        one value and no history — which is the asymmetry with the offline store that makes
        claim 3's comparison bitemporal rather than a straight equality.
        """
        window = self._windows.get(entity_id)
        if not window:
            return None
        values = [value for _, value in window]
        newest = max(event_time for event_time, _ in window)
        return ServedValue(
            entity_id=entity_id,
            feature_id=self.contract.id,
            value=_fold(self.contract.aggregation, values),
            event_time=newest,
            write_time=self._write_times.get(entity_id, newest),
        )

    def forget(self, entity_id: str) -> bool:
        """Remove an entity entirely. The online-store leg of claim 6.

        Returns whether anything was there — the erasure completeness proof needs to distinguish
        "deleted" from "was not present", and a silent no-op reported as success is exactly the
        kind of leg that makes a certificate untrue.
        """
        existed = entity_id in self._windows
        self._windows.pop(entity_id, None)
        self._write_times.pop(entity_id, None)
        return existed


def _fold(kind: str, values: list[int]) -> int:
    """The incremental fold. One branch per aggregation, each returning.

    Written separately from the offline aggregator on purpose. A shared helper would make the
    two mechanisms one mechanism with two names, and claim 3 would compare it with itself.
    """
    if kind == "sum":
        return sum(values)
    if kind == "mean":
        total, count = sum(values), len(values)
        # Half away from zero, matching the SQL's ROUND. Python's built-in `round` is banker's
        # rounding and would disagree with Athena on every .5 — one value in a thousand, often
        # enough to look like a flaky test and rare enough to be dismissed as one.
        return (
            (total * 2 + count) // (count * 2)
            if total >= 0
            else -((-total * 2 + count) // (count * 2))
        )
    if kind == "max":
        return max(values)
    if kind == "min":
        return min(values)
    if kind == "count":
        return len(values)
    if kind == "last":
        return values[-1]
    raise ValueError(f"no online implementation for aggregation {kind!r}")
