"""The offline stream runner: the same core, driven the way the deployed job drives it.

One function, `run`, which takes deliveries and returns everything the pipeline produced. Both
claim harnesses use it, the settlement path uses it, and ADR-0003's tier-two test asserts that
the real PyFlink job produces the same bytes from the same input. That last sentence is why
this exists as a named thing rather than as three similar loops in three test files: an
equivalence test needs one side to be equivalent *to*.

It is not part of `core/`. The core decides what the answers are; this decides the order things
are asked in, which is the adapter's job — and keeping it out means the core's purity gate has
nothing to say about a module that legitimately holds a pipeline's shape.

## The ordering that matters

**Normalise, quarantine, and only then advance the watermark.** A meter whose clock is three
hours fast is a real device in `docs/SCENARIO.md`, and if its event time reaches the watermark
generator before the skew check does, that one meter drags the watermark three hours into the
future and *every window in the grid closes early* — on incomplete data, with no error
anywhere, producing totals that look entirely normal. This is the single most damaging ordering
mistake available in this system, and it is one line.

**Batch by ingestion second.** A stream operator sees records in arrival order and periodically
fires its timers; processing one record at a time and closing after each would be the same
answers at a hundred times the cost, and processing the whole day at once would never exercise
a partially-advanced watermark. A second is the grain the deployed job checkpoints at.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from watermark.core.normalise import DEFAULT_POLICY as NORMALISATION_POLICY
from watermark.core.normalise import NormalisationPolicy, normalise_meter_reading
from watermark.core.quarantine import Quarantined
from watermark.core.records import MeterReading, Source
from watermark.core.time import Duration, Instant
from watermark.core.watermarks import DEFAULT_POLICY as WATERMARK_POLICY
from watermark.core.watermarks import (
    WatermarkPolicy,
    WatermarkState,
    WatermarkStatus,
    WatermarkView,
    held_back_by,
    observe,
)
from watermark.core.windows import Emission, WindowManager, WindowPolicy, WindowResult
from watermark.lineage.identity import LineageId, of_reading, of_result


@dataclass(frozen=True, slots=True)
class Arrival:
    """One raw payload with everything the runner needs and nothing it does not.

    Structurally identical to `data.generate.Delivery`, and deliberately a separate type: the
    runner is not allowed to depend on the synthetic generator, or the claim harnesses would be
    proving something about the generator.
    """

    raw: str
    ingest_time: Instant
    source: Source
    partition: str


@dataclass(frozen=True, slots=True)
class Tick:
    """What the watermark looked like at one point in the run, and what it allowed.

    The evidence for claim 1. A run that published nothing for forty minutes is either broken or
    correctly refusing, and the only difference is whether it can say which — so every
    observation is recorded with its status, its culprit and its lag, and the harness asserts
    against the sequence rather than against the final state.
    """

    at: Instant
    status: WatermarkStatus
    watermark: Instant | None
    holding_back: str | None
    idle: tuple[str, ...]
    lag: Duration
    arrivals: int
    published: int
    restated: int


@dataclass(frozen=True, slots=True)
class RunResult:
    """Everything one pass over the deliveries produced."""

    published: tuple[WindowResult, ...] = ()
    restated: tuple[WindowResult, ...] = ()
    confirmed: tuple[WindowResult, ...] = ()
    quarantined: tuple[Quarantined, ...] = ()
    ticks: tuple[Tick, ...] = ()
    #: The lineage id of every published or restated result, keyed by (meter, interval,
    #: revision). Derived from the readings that contributed, so a replay reproduces it.
    lineage: dict[tuple[str, int, int], LineageId] = field(default_factory=dict)

    @property
    def totals(self) -> dict[tuple[str, int], int]:
        """The final energy for each meter-interval, after every restatement.

        What a settlement report reads. Restatements are applied in revision order, so the last
        word wins — which is what a restatement is.
        """
        latest: dict[tuple[str, int], tuple[int, int]] = {}
        for result in (*self.published, *self.restated, *self.confirmed):
            key = (result.meter_id, result.interval_start.epoch_millis)
            if key not in latest or result.revision >= latest[key][0]:
                latest[key] = (result.revision, result.energy_wh)
        return {key: energy for key, (_, energy) in latest.items()}

    @property
    def stalled_ticks(self) -> tuple[Tick, ...]:
        return tuple(tick for tick in self.ticks if tick.status is WatermarkStatus.STALLED)

    @property
    def held_back_ticks(self) -> tuple[Tick, ...]:
        return tuple(tick for tick in self.ticks if tick.status is WatermarkStatus.HELD_BACK)


@dataclass(frozen=True, slots=True)
class RunPolicy:
    """Everything the run's behaviour depends on, in one place a test can vary."""

    normalisation: NormalisationPolicy = NORMALISATION_POLICY
    watermark: WatermarkPolicy = WATERMARK_POLICY
    window: WindowPolicy = field(default_factory=WindowPolicy)


def run(
    arrivals: Iterable[Arrival],
    partitions: Sequence[str],
    policy: RunPolicy | None = None,
) -> RunResult:
    """Drive the core over a stream of arrivals and return everything it produced.

    `partitions` is declared rather than discovered. A partition the watermark generator has
    never heard of cannot hold anything back, so a substation that is down for the whole run
    would be silently excluded and every window would close without it — see
    `WatermarkState.declare`.
    """
    settings = policy or RunPolicy()
    state = WatermarkState.declare(partitions)
    manager = WindowManager(settings.window)

    quarantined: list[Quarantined] = []
    published: list[WindowResult] = []
    restated: list[WindowResult] = []
    confirmed: list[WindowResult] = []
    ticks: list[Tick] = []
    lineage: dict[tuple[str, int, int], LineageId] = {}
    contributing: dict[tuple[str, int], list[LineageId]] = {}
    previous_view: WatermarkView | None = None

    # Bucketed by ingestion second, and **not sorted within a bucket**.
    #
    # The first version sorted the whole stream by `(ingest_time, raw)` before processing, and
    # gate-proof caught what that hid: with a global sort in front of it, a deduplication rule
    # that kept whichever copy arrived first was still perfectly deterministic, so claim 2's
    # shuffle test passed against a pipeline that had the very defect the claim is about. The
    # sort was doing the work the core was supposed to be proving it did not need.
    #
    # A real operator sees records in whatever order the partitions deliver them. Buckets are
    # visited in time order — a watermark cannot go backwards — but what is inside one arrives
    # as it arrived, so shuffling the input genuinely varies what the core is asked.
    buckets: dict[int, list[Arrival]] = {}
    for arrival in arrivals:
        buckets.setdefault(arrival.ingest_time.epoch_millis // 1000, []).append(arrival)

    for second in sorted(buckets):
        group = buckets[second]
        accepted: list[tuple[Arrival, MeterReading]] = []
        refused: list[Quarantined] = []

        for arrival in group:
            outcome = normalise_meter_reading(
                arrival.raw, arrival.ingest_time, arrival.source, settings.normalisation
            )
            if isinstance(outcome, Quarantined):
                refused.append(outcome)
                continue
            # The arrival is carried alongside its reading rather than matched back afterwards.
            # Normalisation drops records, so the nth reading is not the nth arrival, and any
            # scheme that re-pairs them by position or by searching the payload is a scheme
            # that attributes an event time to the wrong partition the first time a quarantine
            # shifts the indices.
            accepted.append((arrival, outcome))

        readings = [reading for _, reading in accepted]

        # Only readings that survived normalisation reach the watermark. See the module
        # docstring: the three-hour-fast meter must not be allowed to close the whole grid's
        # windows before its skew is noticed.
        state, view = observe(
            state,
            [(arrival.partition, reading.event_time) for arrival, reading in accepted],
            Instant(second * 1000),
            settings.watermark,
        )
        view = held_back_by(view, previous_view)
        previous_view = view

        for reading in readings:
            refusal = manager.admit(reading)
            if refusal is not None:
                refused.append(refusal)
                continue
            contributing.setdefault(
                (reading.meter_id, reading.interval_start.epoch_millis), []
            ).append(of_reading(reading))

        # The side output is emitted in content order, like everything else this pipeline
        # produces. Quarantines were appended in arrival order until claim 2's shuffle test
        # failed on it: the *set* of refusals was identical and their sequence was not, which
        # is byte-identical output being false while every number and every reason is right.
        quarantined.extend(sorted(refused, key=lambda item: (item.reason.value, item.payload)))

        emission = manager.close(view)
        _record_lineage(emission, contributing, lineage)
        published.extend(emission.published)
        restated.extend(emission.restated)
        confirmed.extend(emission.confirmed)

        ticks.append(
            Tick(
                at=Instant(second * 1000),
                status=view.status,
                watermark=view.watermark,
                holding_back=view.holding_back,
                idle=view.idle,
                lag=view.lag,
                arrivals=len(group),
                published=len(emission.published),
                restated=len(emission.restated),
            )
        )

    return RunResult(
        published=tuple(published),
        restated=tuple(restated),
        confirmed=tuple(confirmed),
        quarantined=tuple(quarantined),
        ticks=tuple(ticks),
        lineage=lineage,
    )


def _record_lineage(
    emission: Emission,
    contributing: dict[tuple[str, int], list[LineageId]],
    lineage: dict[tuple[str, int, int], LineageId],
) -> None:
    for result in (*emission.published, *emission.restated, *emission.confirmed):
        key = (result.meter_id, result.interval_start.epoch_millis)
        lineage[(*key, result.revision)] = of_result(result, contributing.get(key, ()))
