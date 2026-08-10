"""The Flink callbacks. Each one translates and delegates; none of them decides.

Every function here takes Flink's vocabulary in, hands the core plain data, and hands Flink the
core's answer back. Nothing in this module compares an event time to anything, chooses a
window, or judges a record late — those are answers, and answers belong to `watermark.core`.

The shape is what `scripts/check_adapter_is_thin.py` enforces. A callback that grew a condition
would be the boundary dissolving in the one place no offline test looks, because the offline
tests exercise the core directly and would keep passing.

**PyFlink is imported lazily, inside the classes that need it.** The module has to be importable
on a machine with no JVM: `make test`, every claim gate and `make preflight --fast` run without
the `flink` extra, and an import at module scope would make this file the one thing that breaks
a credential-free, JVM-free install.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from watermark.core.normalise import NormalisationPolicy, normalise_meter_reading
from watermark.core.quarantine import Quarantined
from watermark.core.records import BATCH_GRAIN, MeterReading, Source
from watermark.core.time import Instant
from watermark.core.watermarks import (
    WatermarkPolicy,
    WatermarkState,
    WatermarkView,
    held_back_by,
    observe,
)
from watermark.core.windows import Emission, WindowManager, WindowPolicy


@dataclass(frozen=True, slots=True)
class Envelope:
    """One Kinesis record as the deserialiser hands it over.

    The partition comes from the record's own partition key, which the IoT topic rule set from
    the topic — which the device policy constrained to the device's own. It is therefore a fact
    established by the broker rather than a claim in the payload, and it is carried alongside
    rather than parsed out.
    """

    raw: str
    ingest_millis: int
    partition: str
    source: str


def normalise(envelope: Envelope, policy: NormalisationPolicy) -> MeterReading | Quarantined:
    """One record into one reading, or into a refusal with a reason.

    `ingest_millis` comes from Flink's record metadata and is passed *in*. The core may not read
    a clock, and an adapter that read one would make the job's output depend on when it was
    replayed rather than on what it was replaying.
    """
    return normalise_meter_reading(
        envelope.raw, Instant(envelope.ingest_millis), Source(envelope.source), policy
    )


@dataclass
class MeterWindowOperator:
    """The keyed operator's state and the order it does things in.

    Extracted from the PyFlink class below so that it can be driven directly by a test with no
    JVM. That is not a convenience: it is the seam that lets the equivalence tier compare *this*
    against `watermark.runner.run` rather than comparing two things that merely look alike.

    **The order is the contract.** Normalise, quarantine, advance the watermark, admit, close.
    A record whose clock is three hours fast must be refused before its event time reaches the
    watermark generator; otherwise that one device closes every window in the grid three hours
    early, on incomplete data, with nothing anywhere reporting an error.
    """

    normalisation: NormalisationPolicy
    watermark: WatermarkPolicy
    window: WindowPolicy
    partitions: tuple[str, ...]

    _state: WatermarkState = field(init=False)
    _manager: WindowManager = field(init=False)
    _previous: WatermarkView | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        # Declared, not discovered. A partition the generator has never heard of cannot hold the
        # watermark back, so a substation that is down at start-up would be silently excluded
        # and every window would close without it.
        self._state = WatermarkState.declare(self.partitions)
        self._manager = WindowManager(self.window)

    def process(
        self, envelopes: Iterable[Envelope], at: Instant
    ) -> tuple[Emission, tuple[Quarantined, ...], WatermarkView]:
        """One batch, in the order that matters."""
        accepted: list[tuple[Envelope, MeterReading]] = []
        refused: list[Quarantined] = []

        for envelope in envelopes:
            outcome = normalise(envelope, self.normalisation)
            if isinstance(outcome, Quarantined):
                refused.append(outcome)
                continue
            accepted.append((envelope, outcome))

        self._state, view = observe(
            self._state,
            [(envelope.partition, reading.event_time) for envelope, reading in accepted],
            at,
            self.watermark,
        )
        view = held_back_by(view, self._previous)
        self._previous = view

        for _, reading in accepted:
            refusal = self._manager.admit(reading)
            if refusal is not None:
                refused.append(refusal)

        emission = self._manager.close(view)
        # Content order, like everything else this pipeline emits. Arrival order is an accident
        # of partitioning; claim 2 is a claim about bytes.
        ordered = tuple(sorted(refused, key=lambda item: (item.reason.value, item.payload)))
        return emission, ordered, view

    def current_view(self) -> WatermarkView | None:
        """The last view the core produced, for Flink's periodic watermark emit.

        `None` before the first batch. Emitting a watermark before anything has been seen would
        assert that nothing earlier will arrive, which is exactly the assertion an unstarted
        pipeline has no basis for.
        """
        return self._previous


def build_process_function(operator: MeterWindowOperator):
    """Wrap the operator in the PyFlink class Flink actually calls.

    Built by a factory rather than declared at module scope so that importing this file costs
    nothing without PyFlink installed. The class body is the translation and nothing else: it
    buffers a record, and on a timer it hands the batch to the operator and emits whatever came
    back.
    """
    from pyflink.datastream import KeyedProcessFunction  # noqa: PLC0415

    class _MeterWindowFunction(KeyedProcessFunction):
        """Buffer, fire on a timer, delegate, emit. No decision of its own."""

        def __init__(self) -> None:
            self._buffer: list[Envelope] = []

        def process_element(self, value, ctx):
            # Destructured by name rather than indexed. `value[2]` is a tuple position, and the
            # adapter gate is right to refuse it: a positional read is a place where the row
            # shape and the code drift silently, and the drift would put the source string in
            # the partition field — where it would key every record onto one shard.
            raw, partition, source = value

            # **Processing time, not `ctx.timestamp()`.** With no watermark strategy attached
            # there is no timestamp assigner, so `ctx.timestamp()` is `None` — and `None //
            # grain` killed the Python worker with no traceback anywhere, leaving a job that
            # restarted every ten seconds while the source kept polling happily.
            #
            # It is also the right value rather than a substitute for one. `ingest_millis` is
            # *when the record arrived*, which at the edge of a stream is processing time; the
            # event time lives inside `raw` and the core reads it there. Taking arrival from a
            # clock the framework owns and event time from the payload is the same separation
            # the whole project rests on.
            now = ctx.timer_service().current_processing_time()
            self._buffer.append(
                Envelope(raw=raw, ingest_millis=now, partition=partition, source=source)
            )

            # A **processing-time** timer for the same reason. An event-time timer fires when a
            # watermark passes it, and this job emits no watermarks — so every timer registered
            # here would have waited for ever. The batch boundary is a transport concern: it
            # bounds how long a record sits in a buffer, and nothing about it is a claim on
            # event time. The grain is `BATCH_GRAIN` from the core rather than a literal, so the
            # offline runner and this one cannot disagree about how much work a restart repeats.
            grain = BATCH_GRAIN.millis
            ctx.timer_service().register_processing_time_timer((now // grain + 1) * grain)

        def on_timer(self, timestamp, ctx):
            batch, self._buffer = self._buffer, []
            if not batch:
                return
            emission, refused, _ = operator.process(batch, Instant(timestamp))
            for result in (*emission.published, *emission.restated, *emission.confirmed):
                yield ("result", result)
            for quarantined in refused:
                yield ("quarantine", quarantined)

    return _MeterWindowFunction()


# `build_watermark_generator` used to live here and is deliberately gone.
#
# It returned `WatermarkStrategy.for_generator(...)`, which does not exist: Flink's own
# documentation states that *"the Python API for Apache Flink does not support custom watermark
# generation."* There is no way to emit a watermark from Python at all.
#
# Removed rather than replaced with `for_bounded_out_of_orderness`, which would have worked and
# would have moved the one decision this project exists to own — when a window may close —
# inside a framework where no offline test can read it. `MeterWindowOperator` below computes
# the watermark, the lateness and the closure from the core, and Flink carries the records.
# See the comment in `job.py` where the strategy used to be attached.
