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
            self._buffer.append(
                Envelope(
                    raw=raw,
                    ingest_millis=ctx.timestamp(),
                    partition=partition,
                    source=source,
                )
            )
            # The next batch boundary. The grain is `BATCH_GRAIN`, from the core, not a literal
            # here: it bounds how late a decision can be for a reason unrelated to data, which
            # makes it a semantic decision — and the offline runner batches on the same name, so
            # the two cannot disagree about how much work a restart repeats.
            grain = BATCH_GRAIN.millis
            ctx.timer_service().register_event_time_timer((ctx.timestamp() // grain + 1) * grain)

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


def build_watermark_generator(operator: MeterWindowOperator):
    """Flink's watermark hook, delegating every judgement to the core.

    It holds no threshold. Flink asks "what is the watermark now?"; `watermark.core.watermarks`
    answers from the event times it has been shown, and this is the translation between the two
    vocabularies. `for_generator` rather than `for_bounded_out_of_orderness` for exactly this
    reason — the convenience constructor would hold the bound inside Flink, where no offline
    test can read it, and `check_adapter_is_thin.py` refuses it by name.
    """
    from pyflink.common.watermark_strategy import (  # noqa: PLC0415
        TimestampAssigner,
        WatermarkStrategy,
    )

    class _FromTheRecord(TimestampAssigner):
        """Event time comes off the record, never off the machine."""

        def extract_timestamp(self, value, record_timestamp):
            return record_timestamp

    class _FromTheCore:
        """Emits whatever `watermark.core.watermarks` says the watermark is.

        It asks the operator, which has already folded every *accepted* event time in — so
        Flink's watermark and the core's are the same number by construction rather than by two
        implementations happening to agree. The generator holds no bound, no idleness timer and
        no threshold of its own; that is the whole reason it exists instead of a convenience
        constructor.
        """

        def on_event(self, event, event_timestamp, output):
            # Nothing. Advancing here would advance on a record the skew check has not seen,
            # which is the ordering mistake that lets one device three hours fast close every
            # window in the grid early.
            return

        def on_periodic_emit(self, output):
            from pyflink.common.watermark_strategy import Watermark  # noqa: PLC0415

            view = operator.current_view()
            if view is None or view.watermark is None or not view.status.may_close_windows:
                return
            output.emit_watermark(Watermark(view.watermark.epoch_millis))

    return WatermarkStrategy.for_generator(_FromTheCore()).with_timestamp_assigner(_FromTheRecord())
