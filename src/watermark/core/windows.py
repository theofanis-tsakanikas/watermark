"""Windows: what is accumulated, when it may be published, and what happens after.

The rule the project is named for lives in one line of this module — `_publish` is reachable
only from `close`, and `close` only acts on windows the watermark permitted. Everything else
here exists to make that line survive contact with late data.

## A window is published, not finished

`docs/SCENARIO.md` has a batch drop arriving up to three days after the interval it measures,
and it changes a total that has already been billed. Doctrine 4 says a correction never erases
what was previously stated. So closing a window is not the end of it: a window can be published
at revision 0, and republished at revision 1 three days later, with the prior value, the delta
and the cause all recoverable.

That is why results carry a `revision` and a `supersedes`, and why a restatement is an ordinary
output of this module rather than a repair job bolted on beside it. A separate backfill path
would be a second implementation of the same arithmetic, and the two would disagree — which
`docs/SCENARIO.md` lists as its own pathology.

## Nothing is emitted in arrival order

Everything this module emits is sorted by content — `(interval_start, meter_id)` — never by the
sequence records happened to arrive in. Claim 2 asserts byte-identical output from shuffled
input, and emission order is the easiest way to fail it while every individual number is right.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from watermark.core.dedup import collapse
from watermark.core.quarantine import Quarantined, Reason
from watermark.core.records import METER_INTERVAL, MeterReading
from watermark.core.time import Duration, Instant
from watermark.core.watermarks import WatermarkStatus, WatermarkView

#: Three days plus a margin, because three days is the legacy head-end's worst case in
#: `docs/SCENARIO.md`. A module constant rather than an inline call in the dataclass default,
#: so the value has one name and appears in one place.
DEFAULT_ALLOWED_LATENESS = Duration.of_days(4)


@dataclass(frozen=True, slots=True)
class WindowPolicy:
    """How long a closed window stays open to correction."""

    #: Length of the window. The metering interval, and it is a parameter rather than a
    #: constant because the settlement grain is a different window over the same code.
    length: Duration = METER_INTERVAL

    #: How long after its interval ends a reading may still restate it. Anything later is
    #: quarantined as `TOO_LATE_FOR_WINDOW` — recoverable, so it is kept and can be reprocessed
    #: on a human decision, but it does not silently move a number that has been invoiced.
    allowed_lateness: Duration = DEFAULT_ALLOWED_LATENESS


@dataclass(frozen=True, slots=True)
class WindowResult:
    """One meter's total for one interval, as published.

    Carries the arithmetic *and* the circumstances. A total is not just a number: whether a
    substation was excluded when it was computed, how many duplicates were suppressed to reach
    it and whether it supersedes an earlier statement are all things somebody will need, and
    all things that are unrecoverable if they were only ever logged.
    """

    meter_id: str
    interval_start: Instant
    energy_wh: int

    #: How many raw readings reached the window, before collapsing.
    readings: int
    duplicates_suppressed: int
    corrections_absorbed: int

    #: The watermark that permitted the publication. This is the evidence for claim 1: a result
    #: with a watermark earlier than its own interval end could not have been produced by this
    #: module, and a recording that contains one is a recording of a bug.
    closed_at: Instant
    #: The watermark's condition at publication. `ADVANCING_WITH_IDLE` means the grid was
    #: closing windows with a partition excluded, and this total was computed in that state.
    watermark_status: WatermarkStatus
    idle_partitions: tuple[str, ...]

    #: When the winning copy of this reading was first ingested — the answer to "when could we
    #: first have known this number?".
    #:
    #: It is what makes the deduplication rule *observable*. `gate-proof` planted a rule that
    #: kept whichever copy arrived first and claim 2 accepted it, because at that point nothing
    #: a result carried came from the winning copy: two retries have the same energy, so the
    #: choice between them was invisible and therefore untestable. A determinism rule nothing
    #: downstream can see is a determinism rule nobody can prove, and it stops being true the
    #: first time somebody simplifies it.
    first_seen_at: Instant

    #: 0 for the first publication, 1 for the first restatement, and so on.
    revision: int = 0
    #: What this replaces. `None` at revision 0. Doctrine 4: the prior value survives.
    supersedes: int | None = None
    #: Why it was restated. `None` at revision 0.
    restatement_cause: str | None = None

    @property
    def interval_end(self) -> Instant:
        return self.interval_start.plus(METER_INTERVAL)

    @property
    def delta_wh(self) -> int:
        """How much this restatement moved the number. Zero at revision 0."""
        return 0 if self.supersedes is None else self.energy_wh - self.supersedes

    def sort_key(self) -> tuple[int, str, int]:
        """Content order, not arrival order. See the module docstring."""
        return (self.interval_start.epoch_millis, self.meter_id, self.revision)


@dataclass(frozen=True, slots=True)
class Emission:
    """Everything one call to `close` produced.

    A single return value rather than three, because the three are one transaction: a
    restatement and the quarantine of the reading that was too late to cause one are the same
    batch of late data being dealt with, and a caller that can forget to look at one of them
    will.
    """

    published: tuple[WindowResult, ...] = ()
    restated: tuple[WindowResult, ...] = ()
    #: Late data arrived, was processed, and the published total did not move — a retry of a
    #: reading already counted. Reported rather than silent, because "nothing changed" and
    #: "nothing was looked at" are the same absence in a log and very different facts. It is
    #: also the honest answer to a duplicate arriving three days late: the number stands.
    confirmed: tuple[WindowResult, ...] = ()
    quarantined: tuple[Quarantined, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.published or self.restated or self.confirmed or self.quarantined)


@dataclass
class WindowManager:
    """Open windows, published windows, and the rule between them.

    Mutable, deliberately: this is the shape Flink keyed state binds to, and pretending
    otherwise would mean rebuilding a dictionary of a quarter of a million meters on every
    record. What matters for claim 2 is not immutability but *determinism* — every transition
    here depends only on the records and the watermark, never on arrival order, a clock or a
    random source, and `scripts/check_core_is_pure.py` enforces the last three.
    """

    policy: WindowPolicy = field(default_factory=WindowPolicy)

    #: Accumulating readings, keyed by (meter_id, interval_start millis).
    _open: dict[tuple[str, int], list[MeterReading]] = field(default_factory=dict, init=False)
    #: The last published result per key, kept so a restatement can state what it supersedes.
    _published: dict[tuple[str, int], WindowResult] = field(default_factory=dict, init=False)
    #: The reading that won each published window.
    #:
    #: Kept so that a late batch is collapsed against *everything* seen for the interval rather
    #: than against itself. Without it, a duplicate arriving three days late would be the only
    #: reading in its bag, would win by default, and would be published as a restatement to
    #: exactly the same number — a revision that moved nothing, in a settlement report, for a
    #: retry. One reading per published window, not the whole bag: the winner plus the new
    #: arrivals is all `collapse` needs to reach the same answer.
    _winner: dict[tuple[str, int], MeterReading] = field(default_factory=dict, init=False)

    # ── Admission ────────────────────────────────────────────────────────────

    def admit(self, reading: MeterReading) -> Quarantined | None:
        """Take a reading into its window, or refuse it with a reason.

        Refusal happens in exactly one case: the window has already been published *and* the
        reading is past the allowed lateness. Everything else is accumulated — including a
        reading for a window that has already closed, which becomes a restatement on the next
        `close`.

        Lateness is measured from the reading's own ingestion time, not from the current
        watermark. The watermark is a fact about the whole stream and can be far ahead because
        of other meters; the question here is how long after *its own* interval ended this
        particular record showed up, and the answer has to be the same on every replay.
        """
        key = self._key(reading)
        previous = self._published.get(key)
        if previous is not None and reading.lateness.millis > self.policy.allowed_lateness.millis:
            return Quarantined(
                Reason.TOO_LATE_FOR_WINDOW,
                f"interval {reading.interval_start} was published at revision "
                f"{previous.revision} and this reading arrived {reading.lateness} after the "
                f"interval ended, past the {self.policy.allowed_lateness} allowance; it is "
                "kept rather than discarded, because it is a real measurement — but it does "
                "not silently move a number that has been settled",
                reading.payload_hash,
            )
        self._open.setdefault(key, []).append(reading)
        return None

    def admit_all(self, readings: Iterable[MeterReading]) -> tuple[Quarantined, ...]:
        refused = [
            quarantined for reading in readings if (quarantined := self.admit(reading)) is not None
        ]
        return tuple(refused)

    # ── Publication ──────────────────────────────────────────────────────────

    def close(self, view: WatermarkView) -> Emission:
        """Publish every open window the watermark permits, and nothing else.

        **This is claim 1.** The guard is `view.may_close(...)`, which is false unless the
        watermark exists, is advancing, and has passed the window's end. A window whose
        watermark says HELD_BACK, STALLED, STARVED or UNSTARTED stays open, and the caller gets
        an empty emission rather than a plausible number.

        What the caller does about an empty emission is not decided here. On a grid, waiting
        is not automatically safe (ADR-0001) — the decision path reads the status and takes its
        declared fallback. The window still does not publish.
        """
        if view.watermark is None:
            # UNSTARTED or STARVED: there is no watermark at all, so there is nothing to
            # publish and nothing to restate — not even a `closed_at` to record.
            return Emission()

        published: list[WindowResult] = []
        restated: list[WindowResult] = []
        confirmed: list[WindowResult] = []

        for key in self._closable(view):
            arrivals = self._open.pop(key)
            outcome = self._publish(key, arrivals, view)
            if outcome is None:
                continue
            result, kind = outcome
            {"published": published, "restated": restated, "confirmed": confirmed}[kind].append(
                result
            )

        return Emission(
            published=tuple(sorted(published, key=WindowResult.sort_key)),
            restated=tuple(sorted(restated, key=WindowResult.sort_key)),
            confirmed=tuple(sorted(confirmed, key=WindowResult.sort_key)),
        )

    def _closable(self, view: WatermarkView) -> list[tuple[str, int]]:
        """Which open windows may be acted on now — and the distinction claim 1 turns on.

        **A first publication requires the watermark's permission.** That is the claim: no
        number leaves this system for a window the watermark has not passed.

        **A restatement does not.** The window it revises has already closed; its closure is an
        established fact, recorded in `closed_at` on the result that was published. A late
        correction is a statement about a window that *has* closed, so the guard has nothing to
        say about it — and applying it anyway would mean a settled number cannot be corrected
        while the stream is stalled, which is precisely when corrections arrive. The legacy
        head-end's three-day-late file is a replay of old event times: it advances nothing, so
        it stalls the watermark by construction. Refusing to restate during a stall would make
        the restatement path unreachable by the only data that uses it.

        The lateness allowance is what bounds this, at admission rather than here: a reading
        past it never enters an open window at all.

        Sorted so that the *set* of keys, not a dictionary's insertion order, decides what is
        processed and in what sequence — insertion order is the arrival-order dependency claim
        2 forbids.
        """
        return sorted(
            key
            for key in self._open
            if key in self._published
            or (
                view.status.may_close_windows
                and view.may_close(Instant(key[1]).plus(self.policy.length))
            )
        )

    def _publish(
        self, key: tuple[str, int], arrivals: list[MeterReading], view: WatermarkView
    ) -> tuple[WindowResult, str] | None:
        previous = self._published.get(key)
        earlier_winner = self._winner.get(key)

        # The bag is everything ever counted for this interval, not just what arrived since the
        # last publication. Collapsing the new arrivals on their own would let a late duplicate
        # win by being the only candidate present.
        bag = arrivals if earlier_winner is None else [earlier_winner, *arrivals]
        collapsed = collapse(bag)
        if collapsed.winner is None:  # pragma: no cover — an open window always holds readings
            return None
        assert view.watermark is not None  # guaranteed by may_close_windows above

        if previous is None:
            kind, revision, supersedes, cause = "published", 0, None, None
        elif collapsed.winner.energy_wh == previous.energy_wh:
            # Late data that confirms rather than corrects. Not a revision: doctrine 4 protects
            # what was stated, and restating a number to itself would put a meaningless row in
            # a settlement report and train whoever reads it to skim revisions.
            kind, revision, supersedes, cause = "confirmed", previous.revision, None, None
        else:
            kind = "restated"
            revision = previous.revision + 1
            supersedes = previous.energy_wh
            cause = _restatement_cause(arrivals, previous)

        # Counts are cumulative over the interval's whole history. A revision reporting "1
        # reading" after a late arrival would make the earlier ones look discarded rather than
        # superseded.
        prior_readings = 0 if previous is None else previous.readings
        prior_duplicates = 0 if previous is None else previous.duplicates_suppressed
        prior_corrections = 0 if previous is None else previous.corrections_absorbed
        # The earlier winner is in the bag but was already counted, so only the new arrivals
        # add to the totals — and one of the "duplicates" collapse found may be that winner
        # meeting its own retry, which is a real suppression and is counted once.
        new_duplicates = min(collapsed.duplicates_suppressed, len(arrivals))

        result = WindowResult(
            meter_id=key[0],
            interval_start=Instant(key[1]),
            energy_wh=collapsed.winner.energy_wh,
            first_seen_at=collapsed.winner.ingest_time,
            readings=prior_readings + len(arrivals),
            duplicates_suppressed=prior_duplicates + new_duplicates,
            corrections_absorbed=prior_corrections + len(collapsed.superseded),
            # **The window's closure, not the current watermark**, and the difference only
            # appears on a revision.
            #
            # For a first publication they are the same thing: the watermark that permitted the
            # close is the watermark now. For a restatement they are not, because
            # `_closable` deliberately lets a correction through while the stream is stalled —
            # the three-day-late file advances nothing by construction, so requiring the
            # watermark's permission would make the restatement path unreachable by the only
            # data that uses it.
            #
            # Stamping the current watermark there wrote a `closed_at` *earlier than the
            # window's own interval*, on a row whose column is documented as "the watermark that
            # permitted publication — a row whose closed_at precedes its own interval end could
            # not have come from the core". Two such rows reached the lakehouse and the capture's
            # claim 1 assertion caught them, correctly: the record said a number had been
            # permitted by a watermark that had not reached the window.
            #
            # The window closed once. That instant is a fact about the window, it is what claim 1
            # is checkable against in SQL, and a correction does not move it — doctrine 4 says a
            # restatement supersedes a value without erasing what was stated, and *when it was
            # stated* is part of what was stated.
            closed_at=view.watermark if previous is None else previous.closed_at,
            watermark_status=view.status,
            idle_partitions=view.idle,
            revision=revision,
            supersedes=supersedes,
            restatement_cause=cause,
        )
        self._published[key] = result
        self._winner[key] = collapsed.winner
        return result, kind

    def _key(self, reading: MeterReading) -> tuple[str, int]:
        return (reading.meter_id, reading.interval_start.epoch_millis)

    # ── Inspection ───────────────────────────────────────────────────────────

    @property
    def open_windows(self) -> int:
        return len(self._open)

    def published_result(self, meter_id: str, interval_start: Instant) -> WindowResult | None:
        return self._published.get((meter_id, interval_start.epoch_millis))

    def open_window_starts(self) -> tuple[Instant, ...]:
        return tuple(sorted({Instant(key[1]) for key in self._open}, key=lambda i: i.epoch_millis))


def _restatement_cause(readings: list[MeterReading], previous: WindowResult) -> str:
    """Why a published total moved, in words a settlement report can carry.

    Derived from the records rather than passed in, so that it cannot disagree with them. The
    sources are sorted for the same reason everything else here is: two runs, same bytes.
    """
    sources = sorted({reading.source.value for reading in readings})
    return (
        f"{len(readings)} reading(s) arrived from {', '.join(sources)} after revision "
        f"{previous.revision} was published"
    )
