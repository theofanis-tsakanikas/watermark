"""The watermark, and the four ways it stops being useful.

These are claim 1's cases at the unit level. `evals/watermark/` scores them as a labelled set;
here each one is pinned to a specific status, a specific culprit and a specific number — because
"no exception was raised" is not an assertion about a streaming system.
"""

from __future__ import annotations

import random

import pytest

from watermark.core.time import Duration, Instant
from watermark.core.watermarks import (
    DEFAULT_POLICY,
    WatermarkPolicy,
    WatermarkState,
    WatermarkStatus,
    WatermarkView,
    closable_windows,
    held_back_by,
    observe,
    observe_silence,
)


def at(hour: int, minute: int) -> Instant:
    return Instant.from_iso(f"2026-03-14T{hour:02d}:{minute:02d}:00Z")


def drive(
    partitions: list[str],
    batches: list[list[tuple[str, Instant]]],
    policy: WatermarkPolicy = DEFAULT_POLICY,
    *,
    seconds_apart: int = 60,
) -> tuple[WatermarkState, WatermarkView]:
    """Run a sequence of batches through a declared set of partitions, tracking held-back.

    Batches arrive a minute apart in ingestion time unless a test says otherwise. Ingestion
    time is what a stall is measured in, so a helper that did not advance it would make every
    stall test a test of a stream that arrived in one instant.
    """
    state = WatermarkState.declare(partitions)
    view = WatermarkView(WatermarkStatus.UNSTARTED, None, (), None, Duration.of_millis(0))
    previous: WatermarkView | None = None
    arrived = Instant.from_iso("2026-03-14T09:00:00Z")
    for batch in batches:
        state, view = observe(state, batch, arrived, policy)
        view = held_back_by(view, previous)
        previous = view
        arrived = arrived.plus(Duration.of_seconds(seconds_apart))
    return state, view


class TestStartup:
    def test_nothing_seen_is_unstarted_not_stalled(self) -> None:
        """A system that has not started is not a system that is stuck. Conflating them is how
        an alarm gets muted on day one and stays muted."""
        _, view = drive(["A"], [])
        assert view.status is WatermarkStatus.UNSTARTED
        assert view.watermark is None

    def test_no_window_closes_before_anything_is_seen(self) -> None:
        _, view = drive(["A"], [])
        assert not view.may_close(at(9, 30))

    def test_a_partition_down_at_startup_does_not_freeze_the_grid(self) -> None:
        """Declared and silent is a fact. If a never-spoken partition pinned the watermark at
        the beginning of time, one substation down at start-up would stop every window in the
        grid, forever — the failure this module exists for, met before it even runs."""
        _, view = drive(["A", "B"], [[("A", at(9, 30))]])
        assert view.status is WatermarkStatus.ADVANCING_WITH_IDLE
        assert view.idle == ("B",)


class TestOrdinaryOperation:
    def test_the_watermark_trails_the_slowest_counted_partition(self) -> None:
        _, view = drive(["A", "B"], [[("A", at(9, 30)), ("B", at(9, 20))]])
        assert view.holding_back == "B"
        assert view.watermark == at(9, 18)  # 09:20 minus two minutes of out-of-orderness

    def test_a_window_closes_once_the_watermark_passes_its_end(self) -> None:
        _, view = drive(["A"], [[("A", at(9, 40))]])
        assert view.may_close(at(9, 30))
        assert not view.may_close(at(9, 45))

    def test_closable_windows_come_back_oldest_first(self) -> None:
        """Emission order decides output order, and claim 2 is a claim about bytes."""
        _, view = drive(["A"], [[("A", at(11, 0))]])
        starts = [at(9, 30), at(9, 0), at(9, 15)]
        assert closable_windows(view, starts) == (at(9, 0), at(9, 15), at(9, 30))

    def test_the_batch_is_folded_as_a_set_not_a_sequence(self) -> None:
        batch = [("A", at(9, 10)), ("A", at(9, 40)), ("A", at(9, 20))]
        results = set()
        for seed in range(20):
            shuffled = list(batch)
            random.Random(seed).shuffle(shuffled)
            _, view = drive(["A"], [shuffled])
            results.add(view.watermark)
        assert results == {at(9, 38)}


class TestHeldBack:
    """`docs/SCENARIO.md`'s quiet substation. Nothing fails; the system stops deciding and
    looks exactly like a system with nothing to decide."""

    def test_a_lagging_partition_stops_every_window_and_is_named(self) -> None:
        _, view = drive(
            ["A", "B", "C"],
            [
                [("A", at(9, 10)), ("B", at(9, 12)), ("C", at(9, 11))],
                [("A", at(9, 30)), ("B", at(9, 30))],
                [("A", at(9, 40)), ("B", at(9, 40))],
            ],
        )
        assert view.status is WatermarkStatus.HELD_BACK
        assert view.holding_back == "C"
        assert not view.status.may_close_windows

    def test_being_held_back_will_resolve_itself(self) -> None:
        """Either the laggard speaks or it crosses `idle_after` and is excluded. That is the
        difference from a stall, and it is why the two are separate statuses."""
        _, view = drive(
            ["A", "B"],
            [[("A", at(9, 10)), ("B", at(9, 10))], [("A", at(9, 30))], [("A", at(9, 40))]],
        )
        assert view.status is WatermarkStatus.HELD_BACK
        assert view.status.will_resolve_itself

    def test_the_lag_is_reported_so_the_caller_can_judge_it(self) -> None:
        """No threshold lives here on purpose: curtailment is worthless thirty seconds late and
        settlement is not late until days have passed. One number cannot serve three horizons."""
        _, view = drive(
            ["A", "B"],
            [[("A", at(9, 10)), ("B", at(9, 10))], [("A", at(9, 30))], [("A", at(9, 40))]],
        )
        assert view.lag == Duration.of_minutes(32)  # 09:40 leader against a 09:08 watermark


class TestIdle:
    def test_a_partition_past_the_threshold_is_excluded_and_the_grid_moves_again(self) -> None:
        _, view = drive(
            ["A", "B", "C"],
            [
                [("A", at(9, 10)), ("B", at(9, 10)), ("C", at(9, 10))],
                [("A", at(11, 0)), ("B", at(11, 0))],
            ],
        )
        assert view.status is WatermarkStatus.ADVANCING_WITH_IDLE
        assert view.idle == ("C",)
        assert view.may_close(at(10, 0))

    def test_the_exclusion_travels_with_the_view(self) -> None:
        """A total computed while a substation was excluded has a hole in it, and the result
        has to be able to say so rather than the fact living in a log."""
        _, view = drive(["A", "B"], [[("A", at(9, 10)), ("B", at(9, 10))], [("A", at(11, 0))]])
        assert view.idle == ("B",)

    def test_the_threshold_is_policy(self) -> None:
        eager = WatermarkPolicy(
            out_of_orderness=Duration.of_minutes(2), idle_after=Duration.of_minutes(5)
        )
        _, view = drive(
            ["A", "B"], [[("A", at(9, 10)), ("B", at(9, 10))], [("A", at(9, 30))]], eager
        )
        assert view.idle == ("B",)


class TestStalled:
    def test_records_arriving_with_no_new_event_time_is_a_stall(self) -> None:
        """A replay of old data, or a source whose clocks went backwards. There is no laggard
        to name and no threshold that resolves it."""
        _, view = drive(["A"], [[("A", at(9, 30))]] * 40)
        assert view.status is WatermarkStatus.STALLED
        assert not view.status.will_resolve_itself

    def test_an_ordinary_upload_burst_is_not_a_stall(self) -> None:
        """The failure the first version of this module had, and the reason a stall is measured
        in ingestion time rather than in observations.

        Meters report on a fifteen-minute cadence, so the highest event time moves once per
        interval and stays put for everything in between. Counting observations reported two
        thirds of a healthy day as stalled — and a threshold that fires on the healthy case is
        switched off within a week.
        """
        burst = [[("A", at(9, 30))] for _ in range(30)]
        _, view = drive(["A"], burst, seconds_apart=1)
        assert view.status is not WatermarkStatus.STALLED

    def test_an_empty_batch_never_counts_towards_a_stall(self) -> None:
        """Quiet and stuck are different facts, and an empty batch is the first one."""
        _, view = drive(["A"], [[("A", at(9, 30))], *([[]] * 60)])
        assert view.status is WatermarkStatus.ADVANCING

    def test_a_stalled_stream_closes_nothing(self) -> None:
        _, view = drive(["A"], [[("A", at(11, 0))]] * 40)
        assert not view.may_close(at(9, 30))


class TestStarved:
    def test_every_partition_silent_is_starved(self) -> None:
        state = WatermarkState.declare(["A", "B"])
        view = observe_silence(state)
        assert view.status is WatermarkStatus.STARVED
        assert view.watermark is None
        assert not view.status.will_resolve_itself

    def test_silence_neither_advances_nor_stalls_the_state(self) -> None:
        """Whether silence is an incident is a question about wall-clock time, and wall-clock
        time is the adapter's. This module reports; it does not start a timer."""
        state = WatermarkState.declare(["A"])
        state_after, _ = observe(state, [("A", at(9, 30))], at(9, 31))
        assert observe_silence(state_after).status is WatermarkStatus.ADVANCING


class TestPolicyValidation:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"out_of_orderness": Duration.of_minutes(-1)}, "negative"),
            ({"idle_after": Duration.of_minutes(0)}, "positive"),
            ({"stall_after": Duration.of_minutes(0)}, "stall_after"),
        ],
    )
    def test_an_unusable_policy_is_refused_at_construction(
        self, kwargs: dict[str, object], message: str
    ) -> None:
        base = {
            "out_of_orderness": Duration.of_minutes(2),
            "idle_after": Duration.of_hours(1),
        }
        with pytest.raises(ValueError, match=message):
            WatermarkPolicy(**{**base, **kwargs})  # type: ignore[arg-type]
