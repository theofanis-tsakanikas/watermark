"""**Claim 1** — no decision comes out of a window that has not closed.

Seven labelled situations, each with a specific expected outcome. Six of them are ways the
system could publish a number it has no right to; the seventh is the healthy case, and it is
here because a check that only ever sees failures cannot tell you it is calibrated.

The one that matters most is `quiet_substation`. Nothing fails when a partition goes silent —
no exception, no red metric, no log line. Every other substation's windows simply stop closing,
and the system looks exactly like a system with nothing to decide. So the assertion is not
"it did not crash": it is that the run *named the substation holding it back*, published
nothing for those windows while it was held back, and resumed afterwards.

`ordinary_burst` is the calibration case, and it is here because of a real defect. The first
version of the stall detector counted consecutive observations; meters report every fifteen
minutes, so the highest event time only moves once per interval and an ordinary upload burst
produced hundreds of observations with an unmoved leader. Two thirds of a healthy day was
reported STALLED. A threshold that fires on the case the system is supposed to survive is
switched off within a week, so the healthy case is scored beside the broken ones.
"""

from __future__ import annotations

from collections import Counter

from data import cast
from data.generate import IDLE_FROM, IDLE_UNTIL, generate

from evals.scoring import Case, first_problem, require
from watermark.core.time import Duration
from watermark.core.watermarks import WatermarkStatus
from watermark.runner import Arrival, RunResult, run


def _arrivals() -> list[Arrival]:
    return [
        Arrival(delivery.raw, delivery.ingest_time, delivery.source, delivery.partition)
        for delivery in generate()
    ]


def _day() -> RunResult:
    return run(_arrivals(), cast.SUBSTATIONS)


def quiet_substation() -> str:
    """SUB-03 stops talking for forty minutes. The grid must notice, and must name it."""
    result = _day()
    outage = [
        tick
        for tick in result.ticks
        if IDLE_FROM.epoch_millis <= tick.at.epoch_millis < IDLE_UNTIL.epoch_millis
    ]
    held = [tick for tick in outage if tick.status is WatermarkStatus.HELD_BACK]
    return first_problem(
        require(bool(outage), "the run produced no observations during the outage window"),
        require(
            bool(held),
            "forty minutes with a silent partition produced no HELD_BACK observation: the "
            "system stopped closing windows and reported nothing about why",
        ),
        # Not "only SUB-03 ever holds it back": at any instant *some* partition is the
        # slowest, and during a busy burst that is whoever happens to be a few seconds behind.
        # What distinguishes an outage from ordinary jitter is persistence, so the assertion is
        # that the silent substation is the dominant culprit — which is false, loudly, if the
        # system never noticed it at all.
        require(
            _dominant_culprit(held) == cast.IDLE_SUBSTATION,
            "during the outage the watermark was mostly held back by "
            f"{_dominant_culprit(held)}, not by the substation that had gone silent",
        ),
        require(
            all(tick.published == 0 for tick in held),
            "a window was published while the watermark was held back",
        ),
        # Recovery is checked in the hour after the link comes back, not at the end of the run:
        # the run ends with the legacy head-end's three-day-late file, which is a replay of old
        # event times and is correctly reported STALLED. Looking at the last observations would
        # be asking whether the outage recovered and reading the answer to a different question.
        require(
            any(
                tick.status is WatermarkStatus.ADVANCING
                and IDLE_UNTIL.epoch_millis
                <= tick.at.epoch_millis
                < IDLE_UNTIL.plus(Duration.of_hours(1)).epoch_millis
                for tick in result.ticks
            ),
            "the watermark never resumed advancing in the hour after the link came back",
        ),
    )


def stalled_stream() -> str:
    """A replay of old event times. Nothing will ever close, and the run must say so."""
    result = _day()
    return first_problem(
        require(
            bool(result.stalled_ticks),
            "three days of replayed event times produced no STALLED observation",
        ),
        require(
            all(tick.published == 0 for tick in result.stalled_ticks),
            "a window was published for the first time while the stream was stalled",
        ),
        require(
            all(not tick.status.will_resolve_itself for tick in result.stalled_ticks),
            "a stalled stream was reported as something waiting would fix",
        ),
    )


def ordinary_burst() -> str:
    """The calibration case: a healthy day must not report itself broken."""
    result = _day()
    stalled_in_the_day = [
        tick for tick in result.stalled_ticks if tick.at.epoch_millis < cast.DAY_END.epoch_millis
    ]
    return require(
        not stalled_in_the_day,
        f"{len(stalled_in_the_day)} observations during ordinary operation were reported "
        "STALLED. Meters report on a fifteen-minute cadence, so the highest event time stands "
        "still between intervals by construction — a detector that calls that a stall fires on "
        "the healthy case and gets switched off.",
    )


def a_window_that_must_not_close() -> str:
    """Nothing may be published for an interval the watermark has not passed."""
    result = _day()
    early = [
        published
        for published in result.published
        if published.closed_at.epoch_millis < published.interval_end.epoch_millis
    ]
    return require(
        not early,
        f"{len(early)} results were published with a watermark earlier than their own "
        "interval end — the definition of a decision from a window that has not closed",
    )


def a_window_that_must_close() -> str:
    """The healthy path still has to work, or refusing everything would score perfectly."""
    result = _day()
    return first_problem(
        require(bool(result.published), "a full day of traffic published nothing at all"),
        require(
            len(result.published) > 3000,
            f"only {len(result.published)} windows closed across a day of 40 meters; the "
            "system is refusing far more than the pathologies account for",
        ),
    )


def a_skewed_clock_does_not_close_the_grid() -> str:
    """One meter three hours fast must not drag the watermark with it.

    The most damaging single ordering mistake available here: if a bad event time reaches the
    watermark generator before the skew check does, that one device closes every window in the
    grid three hours early, on incomplete data, with nothing anywhere reporting an error.
    """
    result = _day()
    skew_quarantines = [
        quarantined
        for quarantined in result.quarantined
        if quarantined.reason.value == "clock_skew_future"
    ]
    leaders = [tick.watermark for tick in result.ticks if tick.watermark is not None]
    horizon = cast.DAY_END.plus(Duration.of_days(3)).plus(Duration.of_hours(1))
    return first_problem(
        require(
            len(skew_quarantines) > 100,
            f"only {len(skew_quarantines)} readings were quarantined for clock skew; two "
            "meters report from hours in the future for the whole day",
        ),
        require(
            all(moment.epoch_millis <= horizon.epoch_millis for moment in leaders),
            "the watermark ran past the end of the data: a skewed device advanced it",
        ),
    )


def a_partition_down_at_startup() -> str:
    """A substation that never speaks must be excluded, not allowed to freeze the grid."""
    arrivals = [arrival for arrival in _arrivals() if arrival.partition != cast.IDLE_SUBSTATION]
    result = run(arrivals, cast.SUBSTATIONS)
    idled = [tick for tick in result.ticks if cast.IDLE_SUBSTATION in tick.idle]
    return first_problem(
        require(
            bool(result.published),
            "a substation that never spoke stopped every other substation's windows from "
            "closing, for the whole run, with no error",
        ),
        require(
            bool(idled),
            "the silent partition was never reported as idle, so the totals computed without "
            "it do not say that they are missing it",
        ),
    )


def _dominant_culprit(ticks: list) -> str | None:
    """Which partition held the watermark back for most of these observations."""
    counts = Counter(tick.holding_back for tick in ticks if tick.holding_back)
    return counts.most_common(1)[0][0] if counts else None


CASES: tuple[Case, ...] = (
    Case(
        "quiet_substation",
        "An idle partition holds the global watermark back and every other substation's "
        "windows stop closing — silently. This is claim 1's sharpest case.",
        quiet_substation,
    ),
    Case(
        "stalled_stream",
        "Records keep arriving and the watermark does not move. A decision path that waits "
        "here waits forever, which on a grid means a substation heating up while nobody "
        "decides.",
        stalled_stream,
    ),
    Case(
        "ordinary_burst",
        "The healthy case. A detector that fires on it is switched off within a week, and "
        "then the real one is missed too.",
        ordinary_burst,
    ),
    Case(
        "a_window_that_must_not_close",
        "The claim itself: no published result may carry a watermark earlier than its own "
        "interval end.",
        a_window_that_must_not_close,
    ),
    Case(
        "a_window_that_must_close",
        "Refusing everything would satisfy every other case in this file perfectly.",
        a_window_that_must_close,
    ),
    Case(
        "a_skewed_clock_does_not_close_the_grid",
        "Normalise and quarantine before advancing the watermark. One three-hour-fast device "
        "otherwise closes every window in the grid early, on incomplete data, silently.",
        a_skewed_clock_does_not_close_the_grid,
    ),
    Case(
        "a_partition_down_at_startup",
        "Declared and silent is a fact; unknown is an assumption. A partition that has never "
        "spoken must not pin the watermark at the beginning of time.",
        a_partition_down_at_startup,
    ),
)
