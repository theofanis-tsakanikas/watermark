"""The recovery drill: kill the job mid-window, restore, and prove no double counting.

`PLAN.md` asks for it *"tested rather than written"*, and the honest reading of that in a
repository that never deploys is: test the property the drill exists to establish, offline,
against the same core the deployed job runs — and be explicit about the half a MiniCluster
would add.

**What this establishes.** That replaying the records a restored job would re-read produces the
same published totals, with no interval counted twice. That property lives in the core: it is
deduplication being a reduction over a bag rather than a filter on a stream, and it is exactly
what makes a restart safe.

**What it does not establish.** That Flink's checkpointing restores the state it says it does.
That needs a MiniCluster and belongs in `tests_flink/` beside the equivalence tier — which has
never run anywhere, and `docs/AWS-CONSTRAINTS.md` says why. The gap is stated rather than
papered over.
"""

from __future__ import annotations

from data import cast
from data.generate import generate

from watermark.core.time import Duration, Instant
from watermark.runner import Arrival, run

#: Where the job is killed: **inside an upload burst**, and that is the whole point of the
#: number. Meters report in the first three minutes after each interval boundary, so a crash at
#: 09:37 lands in the quiet gap between bursts and replays nothing — the drill would pass and
#: prove nothing. 09:31:30 is in the middle of the 09:30 burst, which is when a real outage
#: hurts and the only time double counting is possible.
KILLED_AT = Instant.from_iso("2026-03-14T09:31:30Z")

#: How far back a restored job re-reads. A checkpoint is not the instant of the crash: it is
#: the last one taken before it, so everything since is delivered again. One minute is the
#: checkpoint interval configured in `infra/streaming`.
REPLAY_WINDOW = Duration.of_minutes(1)


def _arrivals() -> list[Arrival]:
    return [
        Arrival(delivery.raw, delivery.ingest_time, delivery.source, delivery.partition)
        for delivery in generate()
    ]


def test_a_restart_does_not_double_count() -> None:
    """The property the drill exists for.

    The uninterrupted run is the reference. The interrupted one processes everything, then
    re-processes the window between the last checkpoint and the crash — which is precisely what
    a restored job does. The totals must match exactly.
    """
    arrivals = _arrivals()
    uninterrupted = run(arrivals, cast.SUBSTATIONS).totals

    checkpoint = KILLED_AT.minus(REPLAY_WINDOW)
    replayed = [
        arrival
        for arrival in arrivals
        if checkpoint.epoch_millis <= arrival.ingest_time.epoch_millis <= KILLED_AT.epoch_millis
    ]
    assert replayed, "the fixture delivered nothing in the replay window; the test proves nothing"

    restored = run([*arrivals, *replayed], cast.SUBSTATIONS).totals

    assert restored == uninterrupted, (
        "a restart changed a published total. Deduplication is a reduction over the bag for a "
        "key, so re-delivering records a restored job re-reads must collapse — if it does not, "
        "every restart bills somebody twice."
    )


def test_the_replay_window_is_actually_exercised() -> None:
    """Calibration. A drill that replays nothing passes trivially and forever."""
    arrivals = _arrivals()
    checkpoint = KILLED_AT.minus(REPLAY_WINDOW)
    replayed = [
        arrival
        for arrival in arrivals
        if checkpoint.epoch_millis <= arrival.ingest_time.epoch_millis <= KILLED_AT.epoch_millis
    ]
    assert len(replayed) > 5, f"only {len(replayed)} records replayed; widen the window"


def test_the_restart_is_visible_in_the_counts() -> None:
    """The totals are identical and the *suppression counts* are not, which is the proof.

    Identical totals reached by never seeing the duplicates would be the same assertion passing
    for the wrong reason — a pipeline that dropped the replayed records silently would satisfy
    the first test perfectly.
    """
    arrivals = _arrivals()
    checkpoint = KILLED_AT.minus(REPLAY_WINDOW)
    replayed = [
        arrival
        for arrival in arrivals
        if checkpoint.epoch_millis <= arrival.ingest_time.epoch_millis <= KILLED_AT.epoch_millis
    ]

    before = run(arrivals, cast.SUBSTATIONS)
    after = run([*arrivals, *replayed], cast.SUBSTATIONS)

    suppressed_before = sum(result.duplicates_suppressed for result in before.published)
    suppressed_after = sum(result.duplicates_suppressed for result in after.published)
    assert suppressed_after > suppressed_before, (
        "the replayed records were never seen. Identical totals reached by not looking is not "
        "recovery, it is data loss that happens to balance."
    )
