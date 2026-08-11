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

import base64
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from watermark.core.normalise import NormalisationPolicy, Reason, normalise_meter_reading
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
from watermark.core.windows import Emission, WindowManager, WindowPolicy, WindowResult
from watermark.lineage.identity import LineageId, of_reading, of_result


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
    # An unrecognised source is quarantined, not raised. `Source(...)` refuses anything outside
    # the core's vocabulary — correctly — but a transport that labels a record wrongly is a bad
    # record, and one bad record must not restart the job. The first live run raised
    # `ValueError: 'iot' is not a valid Source` here and crash-looped the application.
    try:
        source = Source(envelope.source)
    except ValueError:
        # `Reason.UNKNOWN_PAYLOAD_SHAPE`, not a new code and not a bare string. The core's
        # docstring is explicit that `reason` is what gets counted and `detail` is what gets
        # read, "so that nobody is ever tempted to encode the specifics into a new reason code"
        # — and a string here raised `AttributeError` where the core sorts refusals by
        # `reason.value`, which crash-looped the job. A transport that cannot say what a record
        # is has not recognised its shape.
        return Quarantined(
            reason=Reason.UNKNOWN_PAYLOAD_SHAPE,
            detail=f"transport labelled the source {envelope.source!r}, which is not one of "
            f"{[member.value for member in Source]}",
            payload=envelope.raw,
        )

    return normalise_meter_reading(envelope.raw, Instant(envelope.ingest_millis), source, policy)


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
    #: Reading lineage ids per open window, keyed by `(meter_id, interval_start_millis)`.
    #:
    #: The same bookkeeping `watermark.runner.run` does, for the same reason and by the same
    #: calls. Without it the deployed pipeline published totals with **no lineage id at all**
    #: while the offline runner minted one for every result — so claim 2, which is a claim about
    #: lineage hashes surviving a replay, was being proved about a path that production did not
    #: take. The declared table has a `lineage_id` column and dbt tests it `not_null`; nothing
    #: was ever going to fill it.
    _contributing: dict[tuple[str, int], list[LineageId]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        # Declared, not discovered. A partition the generator has never heard of cannot hold the
        # watermark back, so a substation that is down at start-up would be silently excluded
        # and every window would close without it.
        self._state = WatermarkState.declare(self.partitions)
        self._manager = WindowManager(self.window)

    def process(
        self, envelopes: Iterable[Envelope], at: Instant
    ) -> tuple[Emission, tuple[Quarantined, ...], WatermarkView, dict[tuple[str, int, int], str]]:
        """One batch, in the order that matters.

        Returns the lineage ids alongside the emission rather than folding them into
        `WindowResult`, because a result is what the core computed and a lineage id is what
        *this* pipeline computed about it. Keeping them apart is what lets the core stay a pure
        function of readings.
        """
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
                continue
            self._contributing.setdefault(
                (reading.meter_id, reading.interval_start.epoch_millis), []
            ).append(of_reading(reading))

        emission = self._manager.close(view)
        lineage = self._lineage_for(emission)
        # Content order, like everything else this pipeline emits. Arrival order is an accident
        # of partitioning; claim 2 is a claim about bytes.
        ordered = tuple(sorted(refused, key=lambda item: (item.reason.value, item.payload)))
        return emission, ordered, view, lineage

    def _lineage_for(self, emission: Emission) -> dict[tuple[str, int, int], str]:
        """Mint an id for every result this batch emitted.

        **Nothing is pruned.** A restatement arrives days after the publication it supersedes
        and must derive from the same parents, so dropping a window's contributors once it has
        closed would give revision 1 a different provenance from revision 0 — which is the
        lineage equivalent of overwriting the prior value, and doctrine 4 forbids it. The cost
        is memory proportional to the intervals one meter has been seen for, bounded by the
        keyed state Flink already holds for the same meter.
        """
        minted: dict[tuple[str, int, int], str] = {}
        for result in (*emission.published, *emission.restated, *emission.confirmed):
            key = (result.meter_id, result.interval_start.epoch_millis)
            minted[(*key, result.revision)] = of_result(result, self._contributing.get(key, ()))
        return minted

    def current_view(self) -> WatermarkView | None:
        """The last view the core produced, for Flink's periodic watermark emit.

        `None` before the first batch. Emitting a watermark before anything has been seen would
        assert that nothing earlier will arrive, which is exactly the assertion an unstarted
        pipeline has no basis for.
        """
        return self._previous


def _line(kind: str, result: WindowResult, view: object, lineage_id: str | None) -> str:
    """One JSON line per outcome, carrying the whole of what the core computed.

    Emitted rather than returned as an object because the sink is a log: a capture's evidence
    has to be readable by a person and greppable by a script, and neither is true of a pickled
    dataclass in a discarded stream.

    **It used to carry nine of `WindowResult`'s fourteen fields**, and the five it dropped were
    not the unimportant ones. `closed_at` is the watermark that permitted publication — the
    column `pipelines/dbt/models/silver/sources.yml` describes as "claim 1, checkable in SQL
    after the fact", and the only thing that makes a published row auditable without re-running
    the stream. `readings`, `duplicates_suppressed` and `corrections_absorbed` are how a total
    is defended against the meter it came from, and `first_seen_at` is what makes the
    deduplication rule observable at all (see `WindowResult`, where a `gate-proof` mutation
    turned on exactly that).

    The core had computed all five on every result for the whole of phase 1. The adapter threw
    them away on the way to the sink, which is the one place no offline test looks — the claim
    harnesses read `WindowResult` directly and stayed green throughout.

    `idle_partitions` comes off the *result*, not the view. They agree at the moment of
    publication, but the result's copy is the state when this window closed and the view's is
    the state now; for a restatement emitted days later those are different facts, and the row
    is a statement about the window.
    """
    return json.dumps(
        {
            "kind": kind,
            "meter": result.meter_id,
            "interval_start": result.interval_start.epoch_millis,
            # A string, because `energy_wh` is an exact integer count of watt-hours and JSON
            # numbers are doubles in most readers. ADR-0004 removed the parity tolerance in
            # favour of a scaled integer; letting the transport round it would put the tolerance
            # back where nobody would look for it.
            "energy_wh": str(result.energy_wh),
            "readings": result.readings,
            "duplicates_suppressed": result.duplicates_suppressed,
            "corrections_absorbed": result.corrections_absorbed,
            "closed_at": result.closed_at.epoch_millis,
            "first_seen_at": result.first_seen_at.epoch_millis,
            "revision": result.revision,
            "supersedes": result.supersedes,
            "restatement_cause": result.restatement_cause,
            "watermark_status": result.watermark_status.value,
            "idle_partitions": list(result.idle_partitions),
            "lineage_id": lineage_id,
            # The watermark's condition *now*, next to the condition when the window closed.
            # For a first publication they are the same; for a restatement they are the two
            # halves of "what did we know then, and what do we know now".
            "observed_status": getattr(getattr(view, "status", None), "value", None),
        },
        default=str,
    )


#: The evidence channel.
#:
#: `.print()` writes to the task manager's stdout, which Managed Flink does not forward to the
#: application log — the job ran, read 51,744 records and produced not one visible line. A
#: capture whose output cannot be read is a capture that proves nothing, so every outcome is
#: also logged through the standard logger, which Managed Flink *does* collect.
_EVIDENCE = logging.getLogger("watermark.evidence")


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
            #: Rows the transport could not decode at all. Held rather than dropped: a record
            #: that vanishes silently is the one nobody can account for afterwards.
            self._undecodable: list[tuple[str | None, str | None]] = []
            #: The last watermark condition reported, so that only changes are reported.
            self._reported: tuple[str, str | None] | None = None

        def process_element(self, value, ctx):
            # Destructured by name rather than indexed. `value[2]` is a tuple position, and the
            # adapter gate is right to refuse it: a positional read is a place where the row
            # shape and the code drift silently, and the drift would put the source string in
            # the partition field — where it would key every record onto one shard.
            raw, partition, source = value

            # **A record this adapter cannot read is data, not an exception.**
            #
            # `raw` arrives base64 from the IoT rule's `encode(*, 'base64')`, and it can be
            # absent: a stream retains records for a day, so a rule whose shape changed leaves
            # the older shape sitting in front of the job. `b64decode(None)` raised, the Python
            # worker died, and the job restarted into the same record — a crash loop caused by
            # one malformed message, which is the failure a quarantine exists to prevent.
            #
            # So it is quarantined here and the batch continues. Decoding is transport; what the
            # bytes *mean* is still `normalise`'s question, in the core, and a record that never
            # reaches the core cannot be quarantined by it.
            try:
                raw = base64.b64decode(raw).decode("utf-8")
            except (TypeError, ValueError, UnicodeDecodeError):
                self._undecodable.append((partition, source))
                return

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
            undecodable, self._undecodable = self._undecodable, []

            for partition, source in undecodable:
                # Emitted, not merely constructed. This block used to build the line and drop
                # it: the local was assigned once per undecodable record and never logged,
                # never yielded and never read. A record the transport could not decode
                # vanished without trace — from the very branch whose comment says that a
                # record which vanishes silently is the one nobody can account for afterwards.
                quarantine_line = json.dumps(
                    {
                        "kind": "quarantine",
                        "reason": "undecodable_transport",
                        "partition": partition,
                        "source": source,
                    }
                )
                _EVIDENCE.info(quarantine_line)
                yield quarantine_line

            if not batch:
                return
            emission, refused, view, lineage = operator.process(batch, Instant(timestamp))

            # **The watermark reports its own condition, and not only when it publishes.**
            #
            # Everything below this speaks about a *result*, so the job was silent in exactly
            # the state claim 1 exists to make visible: while a partition holds the watermark
            # back, no window closes, so no line was emitted, so a held-back grid and a healthy
            # quiet one produced identical output — nothing. `README.md` listed `held_back`,
            # `stalled` and `starved` as states proved offline and never induced in the cloud;
            # they could not have been induced, because there was no way for the deployed job to
            # say so.
            #
            # On change rather than on every batch. The grain is a second, and a line a second
            # per key is a log nobody reads and a bill somebody notices; a *transition* is the
            # event — the moment SUB-03 went quiet and the moment it came back.
            condition = (view.status.value, view.holding_back)
            if condition != self._reported:
                self._reported = condition
                status_line = json.dumps(
                    {
                        "kind": "watermark",
                        "status": view.status.value,
                        "holding_back": view.holding_back,
                        "idle_partitions": list(view.idle),
                        "lag_millis": view.lag.millis,
                        "watermark": getattr(view.watermark, "epoch_millis", None),
                        "may_close_windows": view.status.may_close_windows,
                        "at": timestamp,
                    }
                )
                _EVIDENCE.info(status_line)
                yield status_line

            # JSON strings, not tuples of dataclasses. `.process()` defaults to pickling its
            # output, which means every worker must be able to import the core's classes to
            # *serialise* a result — a second reason to fail that has nothing to do with the
            # computation. A string has none of that, and it is what a sink can show a human.
            #
            # The watermark status travels on every line. It is the evidence claim 1 is about:
            # a published window says which watermark let it out, so a reader can tell a closed
            # window from one that was let through.
            for kind, results in (
                ("published", emission.published),
                ("restated", emission.restated),
                ("confirmed", emission.confirmed),
            ):
                for result in results:
                    key = (result.meter_id, result.interval_start.epoch_millis, result.revision)
                    line = _line(kind, result, view, lineage.get(key))
                    _EVIDENCE.info(line)
                    yield line
            for quarantined in refused:
                quarantine_line = json.dumps(
                    {
                        "kind": "quarantine",
                        # `.value`, because `Reason` is an enum and `json.dumps` refuses it —
                        # "Object of type Reason is not JSON serializable" crash-looped the job
                        # one line short of working. `default=str` below is the belt to that
                        # brace: a line of evidence must never be the thing that stops the run.
                        "reason": quarantined.reason.value,
                        "detail": quarantined.detail,
                    },
                    default=str,
                )
                _EVIDENCE.info(quarantine_line)
                yield quarantine_line

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
