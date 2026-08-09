"""The Flink callbacks. Each one delegates to the core and returns.

Every function here is a translation: Flink's types in, core types out, core answer back.
Nothing in this module compares an event time to anything, chooses a window, or decides that a
record is late — those are answers, and answers belong to `watermark.core`.

The shape is deliberate and it is what `scripts/check_adapter_is_thin.py` enforces. A callback
that grew a condition would be the boundary dissolving in the one place no offline test looks,
because the offline tests exercise the core directly and would keep passing.
"""

from __future__ import annotations

from collections.abc import Iterable

from watermark.core.normalise import NormalisationPolicy, normalise_meter_reading
from watermark.core.quarantine import Quarantined
from watermark.core.records import MeterReading, Source
from watermark.core.time import Instant
from watermark.core.watermarks import WatermarkPolicy, WatermarkState, WatermarkView, observe
from watermark.core.windows import Emission, WindowManager


def normalise(
    raw: str, ingest_millis: int, source: str, policy: NormalisationPolicy
) -> MeterReading | Quarantined:
    """One Kinesis record into one reading, or into a refusal with a reason.

    `ingest_millis` is the record's approximate arrival timestamp, which Flink hands the
    deserialiser. It is passed *in* rather than read here: the core may not touch a clock, and
    an adapter that read one would make the job's output depend on when it was replayed.
    """
    return normalise_meter_reading(raw, Instant(ingest_millis), Source(source), policy)


def advance_watermark(
    state: WatermarkState,
    events: Iterable[tuple[str, Instant]],
    at: Instant,
    policy: WatermarkPolicy,
) -> tuple[WatermarkState, WatermarkView]:
    """Fold this batch's event times into the watermark state.

    Only readings that survived normalisation reach here. That ordering is the single most
    damaging thing to get wrong in this system — a device three hours fast otherwise drags the
    watermark with it and closes every window in the grid early, on incomplete data, silently —
    and it is enforced by the shape of `MeterStreamFunction.process`, not by a comment.
    """
    return observe(state, events, at, policy)


def admit_and_close(
    manager: WindowManager, readings: Iterable[MeterReading], view: WatermarkView
) -> tuple[Emission, tuple[Quarantined, ...]]:
    """Take readings into their windows and publish whatever the watermark permits.

    The refusals come back sorted by content, like everything else this pipeline emits. Arrival
    order is an accident of partitioning; claim 2 is a claim about bytes.
    """
    refused = manager.admit_all(readings)
    emission = manager.close(view)
    return emission, tuple(sorted(refused, key=lambda item: (item.reason.value, item.payload)))
