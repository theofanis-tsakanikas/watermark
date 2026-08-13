"""The watermark: the system's claim about what it has seen.

This is the module the project is named after. A watermark at time *W* asserts that no record
with an event time before *W* will arrive from now on. Every window closes because a watermark
said it could, so every decision this platform takes rests on that assertion being true — and,
just as importantly, on the system knowing when it *cannot* make the assertion at all.

**No clock is read here, and that is a design decision rather than a constraint.** Flink
detects idleness with processing time; this module works entirely in *event time*, by how far
a partition has fallen behind the others. That makes it a pure function of the records, so
claim 1's cases are unit tests rather than tests that sleep. The one shape event time cannot
see is everything going quiet at once, and for that the adapter — which does have a clock —
calls `observe_silence`. The clock stays outside; what comes in is a fact.

## Four ways a watermark stops being useful, and why they are four and not one

The first draft of this module had one status for the last three, and it was wrong in a way
worth recording: it fired on the case the system is supposed to survive, and it could not name
the case it was supposed to catch.

**Held back.** One partition is lagging. The others advance; the global minimum does not.
Nothing closes. *This is normal for a few seconds and an incident after twenty minutes*, and
nothing in the stream can tell you which — because the answer depends on the decision waiting
on it. A curtailment decision is worthless thirty seconds late; a settlement total is not late
until days have passed. So this status reports **who** is holding it back and **how far behind
they are**, and the decision path decides what that costs it. Putting a threshold here would
be putting one number where three decisions with three horizons need three.

**Idle.** The laggard has fallen so far behind that it is excluded from the minimum, and the
rest of the grid's windows close without it. This is the resolution of *held back*, not a
different problem — but a total computed while a substation was excluded has a hole in it, so
the exclusion travels with the result rather than being logged.

**Stalled.** The *leader* stops advancing while records keep arriving: a replay of old data, a
source whose clocks went backwards, a misconfigured bound. Distinct from held back, because
there is no laggard to name and no threshold that will resolve it. Nothing will close, ever.

**Starved.** Nothing is arriving at all.

`docs/SCENARIO.md` calls the idle source claim 1's sharpest case, and it is: nothing fails, no
exception is raised, no metric goes red. The system stops deciding and it looks exactly like a
system with nothing to decide.

What none of this does is decide what to do about it. On a grid, silence is not the safe state
(ADR-0001): a stalled watermark means the decision path takes its declared conservative
fallback and marks it. Which is why the status is an answer a caller has to handle and not an
exception it can ignore.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from watermark.core.records import METER_INTERVAL
from watermark.core.time import Duration, Instant

#: Twice the metering interval plus a margin. Long enough that an ordinary upload burst — where
#: the highest event time does not move for the length of an interval — is never mistaken for a
#: stall; short enough that a replay of old data is caught within one settlement period.
DEFAULT_STALL_AFTER: Final = Duration.of_minutes(30)


class WatermarkStatus(Enum):
    """What the watermark is currently able to assert."""

    #: Nothing has been seen yet. No window may close; there is no basis on which one could.
    #: Distinct from STALLED, because a system that has not started is not a system that is
    #: stuck, and treating start-up as an incident is how an alarm gets muted.
    UNSTARTED = "unstarted"

    #: Advancing normally.
    ADVANCING = "advancing"

    #: Advancing, with at least one partition excluded for falling too far behind. Windows
    #: close — that is what excluding it was for — and the exclusion travels with the result,
    #: because a total computed while a substation was excluded is a total with a hole in it.
    ADVANCING_WITH_IDLE = "advancing_with_idle"

    #: The leader is advancing; the watermark is not, because a named partition is lagging.
    #: Windows do not close. Whether that is tolerable is the *caller's* judgement, from
    #: `WatermarkView.lag` and its own horizon — see the module docstring.
    HELD_BACK = "held_back"

    #: Records are arriving and even the highest event time seen is not moving. Nothing will
    #: close, and no threshold will rescue it.
    STALLED = "stalled"

    #: Every partition is idle or silent. Nothing is arriving at all.
    #:
    #: **This detects *relative* silence, not absolute silence, and the difference matters.**
    #: `idle` means "further behind the leader than `idle_after`", measured in event time. One
    #: substation going quiet while the others keep reporting is caught, which is the scenario's
    #: case and claim 1's sharpest one. A stream that stops *entirely* is not: the leader stops
    #: moving too, every partition stays the same distance from it, nothing becomes idle, and
    #: the view goes on saying `ADVANCING` about a grid that has said nothing for an hour.
    #:
    #: That is a real limit of this design and it is written here rather than discovered. What
    #: protects the system in that case is not this status — it is the freshness gate, claim 4,
    #: which measures a served value's age against the moment a decision is being taken rather
    #: than against other event times. A frozen watermark leaves every feature ageing past its
    #: budget, and the decision falls back and says so.
    #:
    #: Making absolute silence visible here needs a `silent_after` compared against processing
    #: time, which means this module reads a clock — and it currently reads none, which is what
    #: makes replay identical (claim 2). The trade is real and has not been made.
    STARVED = "starved"

    @property
    def may_close_windows(self) -> bool:
        return self in (WatermarkStatus.ADVANCING, WatermarkStatus.ADVANCING_WITH_IDLE)

    @property
    def will_resolve_itself(self) -> bool:
        """Whether waiting is a strategy.

        HELD_BACK resolves on its own: the laggard speaks, or it crosses `idle_after` and is
        excluded. STALLED and STARVED do not — and a decision path that waits on either waits
        forever, which on this system means a substation heating up while nobody decides.
        """
        return self not in (WatermarkStatus.STALLED, WatermarkStatus.STARVED)


@dataclass(frozen=True, slots=True)
class WatermarkPolicy:
    """How much disorder is tolerated, and how far behind is too far.

    Every value is policy. In phase 2 they arrive from a contract; here they are arguments,
    because a threshold baked into a function is a threshold nobody can vary in a test — and
    the tests that matter for claim 1 are exactly the ones that vary these.
    """

    #: How far behind the highest event time seen the watermark sits. This is the bet: records
    #: later than this are late, and late records go to the side output rather than into a
    #: window that has already closed.
    out_of_orderness: Duration

    #: How far a partition may lag the *fastest* partition before it is excluded from the
    #: global minimum. Event time, so it is a statement about data rather than about patience.
    #:
    #: A real trade-off with no free answer. Too low and a merely slow substation is excluded,
    #: its readings arrive after its windows closed, and they become late data — correct, but
    #: expensive. Too high and one quiet substation stops the entire grid's windows for as long
    #: as the threshold allows.
    idle_after: Duration

    #: How long records may keep arriving with the *leader* unmoved before the stream is called
    #: stalled. Measured in **ingestion** time, taken from the records themselves.
    #:
    #: The first version counted consecutive observations instead, and running it over a day of
    #: real generated traffic showed why that is wrong: meters report on a fifteen-minute
    #: cadence, so the highest event time only moves once per interval, and every ordinary
    #: upload burst produced hundreds of consecutive observations with an unmoved leader. Two
    #: thirds of a normal day was reported STALLED. A threshold that fires on the healthy case
    #: is worse than no threshold, because it is switched off within a week.
    #:
    #: Ingestion time is the right axis and it costs nothing: it is on every record, so the
    #: module stays a pure function of its input and never reads a clock.
    stall_after: Duration = DEFAULT_STALL_AFTER

    def __post_init__(self) -> None:
        if self.out_of_orderness.millis < 0:
            raise ValueError("out-of-orderness cannot be negative; it is a delay, not a lead")
        if not self.idle_after.is_positive:
            raise ValueError("idle_after must be positive; zero would idle every partition")
        if not self.stall_after.is_positive:
            raise ValueError("stall_after must be positive; zero calls every batch a stall")


#: Chosen against the scenario rather than by taste. Two minutes of out-of-orderness covers
#: ordinary burst reordering without holding settlement back. An hour of lag before exclusion
#: is deliberately longer than the scenario's forty-minute quiet substation: excluding a
#: partition puts a hole in every total computed after it, so the threshold should not fire on
#: the case the system is expected to ride out. That the grid's windows do not close for those
#: forty minutes is not hidden — it is HELD_BACK, with the substation named.
DEFAULT_POLICY: Final = WatermarkPolicy(
    out_of_orderness=Duration.of_minutes(2),
    idle_after=Duration.of_hours(1),
)


@dataclass(frozen=True, slots=True)
class WatermarkState:
    """Everything the generator remembers. Small, and derivable from the records alone."""

    #: Highest event time seen per partition. Present with `None` means declared and silent;
    #: absent means never heard of. The difference decides whether a partition can hold the
    #: watermark back at all — see `declare`.
    highest: Mapping[str, Instant | None]
    #: The highest event time seen anywhere, carried so a stall can be recognised.
    previous_leader: Instant | None = None
    #: The ingestion instant at which the leader last moved. A stall is how long ago that was.
    leader_last_moved_at: Instant | None = None

    @staticmethod
    def declare(partitions: Iterable[str]) -> WatermarkState:
        """Start with a known set of partitions, none of which has spoken yet.

        Declaring them matters. A partition the generator has never heard of cannot hold the
        watermark back, so a substation that is down at start-up would be silently excluded and
        every window would close without it — the exact hole this module exists to make
        visible. Declared and silent is a fact; unknown is an assumption.
        """
        return WatermarkState(highest=dict.fromkeys(partitions))


@dataclass(frozen=True, slots=True)
class WatermarkView:
    """The watermark, and the reasons it is what it is."""

    status: WatermarkStatus
    #: `None` whenever there is no basis for one at all — UNSTARTED and STARVED. A caller that
    #: reaches for a number without checking the status gets a `None` rather than a plausible
    #: instant.
    watermark: Instant | None
    #: Partitions excluded for lagging past `idle_after`. Sorted, so two runs produce the same
    #: bytes.
    idle: tuple[str, ...]
    #: The slowest partition still counted — the one pinning the watermark. The answer to "why
    #: has nothing closed for twenty minutes", available at the moment it is asked rather than
    #: reconstructed from logs afterwards.
    holding_back: str | None
    #: How far the watermark trails the highest event time seen anywhere. This is the number a
    #: decision path compares against its own horizon; the module deliberately does not compare
    #: it against one of its own.
    lag: Duration
    #: The highest event time seen in any partition. Carried rather than derived because
    #: `held_back_by` needs it: without knowing whether the *leader* moved, a stream that has
    #: simply gone quiet is indistinguishable from one partition holding everything back — and
    #: the first is normal while the second is the case claim 1 is about.
    leader: Instant | None = None

    def may_close(self, window_end: Instant) -> bool:
        """Whether a window ending at this instant may close.

        The only sanctioned route to that question. A caller comparing `watermark` itself would
        have to remember to check the status first, and one day would not.
        """
        if not self.status.may_close_windows or self.watermark is None:
            return False
        return self.watermark.epoch_millis >= window_end.epoch_millis


def observe(
    state: WatermarkState,
    events: Iterable[tuple[str, Instant]],
    at: Instant,
    policy: WatermarkPolicy = DEFAULT_POLICY,
) -> tuple[WatermarkState, WatermarkView]:
    """Fold a batch of `(partition, event_time)` pairs into the state, and report.

    `at` is the batch's **ingestion** instant, supplied by the caller and read off the records
    rather than off a clock. It is what makes a stall measurable: "records have been arriving
    for half an hour and the highest event time has not moved" is a statement about two times,
    and only one of them is event time.

    Deterministic in the batch as a *set*: the highest event time per partition does not depend
    on the order the batch is iterated in. That is what lets claim 2 shuffle the input and still
    assert the same bytes.

    An empty batch is not an observation. It cannot advance anything and must not count towards
    a stall, or a quiet moment would be indistinguishable from a stuck one.
    """
    # Materialised because the batch is read twice — once to fold, once to know whether there
    # was anything to fold. A generator would be exhausted by the first pass, every batch would
    # look empty, and the stall detector would never fire: a check that passes by never running.
    batch = list(events)

    highest = dict(state.highest)
    for partition, event_time in batch:
        current = highest.get(partition)
        if current is None or event_time.epoch_millis > current.epoch_millis:
            highest[partition] = event_time

    leader = _leader(highest)
    leader_moved = leader is not None and (
        state.previous_leader is None or leader.epoch_millis > state.previous_leader.epoch_millis
    )
    last_moved_at = (
        at if leader_moved or state.leader_last_moved_at is None else (state.leader_last_moved_at)
    )
    stalled_for = at.since(last_moved_at)

    view = _view(highest, policy)
    # An empty batch cannot stall anything: quiet and stuck are different facts, and calling a
    # quiet Sunday a stall is how the alarm gets muted before the real one.
    if (
        batch
        and stalled_for.millis > policy.stall_after.millis
        and view.status not in (WatermarkStatus.UNSTARTED, WatermarkStatus.STARVED)
    ):
        view = WatermarkView(
            WatermarkStatus.STALLED,
            view.watermark,
            view.idle,
            view.holding_back,
            view.lag,
            view.leader,
        )

    return (
        WatermarkState(highest=highest, previous_leader=leader, leader_last_moved_at=last_moved_at),
        view,
    )


def observe_silence(
    state: WatermarkState,
    policy: WatermarkPolicy = DEFAULT_POLICY,
) -> WatermarkView:
    """Report the watermark without folding anything in.

    The one thing event time cannot see is every partition going quiet at once: with no records
    there is nothing for a laggard to lag behind, so nothing is declared idle and the watermark
    simply stops. The adapter, which does have a clock, calls this when it has waited and
    nothing came, and gets back a view it can act on.

    It deliberately neither mutates the state nor counts towards a stall. Whether silence is an
    incident is a question about wall-clock time, and wall-clock time is the adapter's.
    """
    return _view(dict(state.highest), policy)


def _leader(highest: Mapping[str, Instant | None]) -> Instant | None:
    spoken = [moment for moment in highest.values() if moment is not None]
    return max(spoken, key=lambda moment: moment.epoch_millis) if spoken else None


def _view(highest: Mapping[str, Instant | None], policy: WatermarkPolicy) -> WatermarkView:
    leader = _leader(highest)
    if leader is None:
        status = WatermarkStatus.STARVED if highest else WatermarkStatus.UNSTARTED
        return WatermarkView(status, None, tuple(sorted(highest)), None, Duration.of_millis(0))

    # A declared partition that has never spoken lags by definition — infinitely — so it is
    # idle rather than pinning the watermark at the beginning of time. Without this, one
    # substation that is down at start-up stops every window in the grid forever: the failure
    # this module exists for, met at start-up instead of mid-run.
    def lags_too_far(moment: Instant | None) -> bool:
        return moment is None or leader.since(moment).millis > policy.idle_after.millis

    idle = tuple(sorted(partition for partition, moment in highest.items() if lags_too_far(moment)))
    counted = {
        partition: moment
        for partition, moment in highest.items()
        if moment is not None and partition not in set(idle)
    }
    if not counted:
        return WatermarkView(
            WatermarkStatus.STARVED, None, idle, None, Duration.of_millis(0), leader
        )

    holding_back = min(counted, key=lambda partition: counted[partition].epoch_millis)
    watermark = counted[holding_back].minus(policy.out_of_orderness)
    lag = leader.since(watermark)
    status = WatermarkStatus.ADVANCING_WITH_IDLE if idle else WatermarkStatus.ADVANCING
    return WatermarkView(status, watermark, idle, holding_back, lag, leader)


def held_back_by(view: WatermarkView, previous: WatermarkView | None) -> WatermarkView:
    """Re-label an advancing view as HELD_BACK when the watermark did not actually move.

    Separate from `observe` because it needs the previous view, and threading that through the
    state would make the state remember something only a reporting decision cares about.

    The distinction it draws is the one the first version of this module missed: the leader
    moving while the watermark does not is a *named* partition lagging, and it is a different
    thing from the whole stream being stuck. Conflating them means either crying wolf on every
    slow substation or having no signal for the case that matters.
    """
    if previous is None or view.watermark is None or previous.watermark is None:
        return view
    if view.status is not WatermarkStatus.ADVANCING:
        return view
    if view.watermark.epoch_millis > previous.watermark.epoch_millis:
        return view
    # The leader must have moved. If nothing advanced at all, the stream has gone quiet — which
    # is ordinary, and reporting it as one partition obstructing the others would put a culprit
    # on an idle Sunday afternoon. Quiet is `observe_silence`'s question, and it is asked with a
    # clock the core does not have.
    if view.leader is None or previous.leader is None:
        return view
    if view.leader.epoch_millis <= previous.leader.epoch_millis:
        return view
    return WatermarkView(
        WatermarkStatus.HELD_BACK,
        view.watermark,
        view.idle,
        view.holding_back,
        view.lag,
        view.leader,
    )


def closable_windows(
    view: WatermarkView,
    open_window_starts: Iterable[Instant],
    window_length: Duration = METER_INTERVAL,
) -> tuple[Instant, ...]:
    """Which currently open windows the watermark permits closing, oldest first.

    Sorted rather than left in whatever order the caller held them: the order windows close in
    decides the order results are emitted in, and claim 2 is a claim about bytes.
    """
    return tuple(
        sorted(
            (start for start in open_window_starts if view.may_close(start.plus(window_length))),
            key=lambda start: start.epoch_millis,
        )
    )
