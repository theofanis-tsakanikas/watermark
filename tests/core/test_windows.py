"""Publication, restatement, and the line claim 1 is made of."""

from __future__ import annotations

import random

from watermark.core.normalise import payload_hash
from watermark.core.quarantine import Reason
from watermark.core.records import MeterReading, Source
from watermark.core.time import Duration, Instant
from watermark.core.watermarks import (
    WatermarkState,
    WatermarkStatus,
    WatermarkView,
    held_back_by,
    observe,
)
from watermark.core.windows import WindowManager, WindowPolicy

START = Instant.from_iso("2026-03-14T09:15:00Z")


def reading(
    energy_wh: int,
    ingest: str,
    *,
    meter: str = "M1",
    interval: Instant = START,
    source: Source = Source.STREAM,
) -> MeterReading:
    return MeterReading(
        meter_id=meter,
        interval_start=interval,
        event_time=interval,
        ingest_time=Instant.from_iso(ingest),
        energy_wh=energy_wh,
        firmware="fw2",
        source=source,
        payload_hash=payload_hash(meter, interval, energy_wh),
    )


def view_at(moment: str, partitions: tuple[str, ...] = ("S1",)) -> WatermarkView:
    state = WatermarkState.declare(list(partitions))
    _, view = observe(state, [(partitions[0], Instant.from_iso(moment))], Instant.from_iso(moment))
    return view


class TestClaimOne:
    """No decision comes out of a window that has not closed."""

    def test_a_window_the_watermark_has_not_passed_does_not_publish(self) -> None:
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))
        assert manager.close(view_at("2026-03-14T09:20:00Z")).is_empty
        assert manager.open_windows == 1

    def test_it_publishes_once_the_watermark_passes_the_interval_end(self) -> None:
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))
        emission = manager.close(view_at("2026-03-14T09:40:00Z"))
        assert [r.energy_wh for r in emission.published] == [312]

    def test_a_held_back_watermark_publishes_nothing(self) -> None:
        """The quiet substation. Every other meter's window is ready and none of them close —
        which is correct, and is the thing that must not happen silently."""
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))

        # S2 speaks once at 09:20 and then goes quiet. It is not idle — it is only forty
        # minutes behind, well inside the hour — so it still pins the watermark below the
        # window's end while S1 races ahead.
        state = WatermarkState.declare(["S1", "S2"])
        state, _ = observe(
            state,
            [
                ("S1", Instant.from_iso("2026-03-14T09:20:00Z")),
                ("S2", Instant.from_iso("2026-03-14T09:20:00Z")),
            ],
            Instant.from_iso("2026-03-14T09:21:00Z"),
        )
        state, first = observe(
            state,
            [("S1", Instant.from_iso("2026-03-14T09:45:00Z"))],
            Instant.from_iso("2026-03-14T09:46:00Z"),
        )
        state, second = observe(
            state,
            [("S1", Instant.from_iso("2026-03-14T10:00:00Z"))],
            Instant.from_iso("2026-03-14T10:01:00Z"),
        )
        second = held_back_by(second, first)

        assert second.status is WatermarkStatus.HELD_BACK
        assert second.holding_back == "S2"
        assert manager.close(second).is_empty

    def test_a_stalled_watermark_publishes_nothing(self) -> None:
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))
        state = WatermarkState.declare(["S1"])
        arrived = Instant.from_iso("2026-03-14T11:01:00Z")
        for _ in range(5):
            state, view = observe(
                state, [("S1", Instant.from_iso("2026-03-14T11:00:00Z"))], arrived
            )
            arrived = arrived.plus(Duration.of_minutes(15))
        assert view.status is WatermarkStatus.STALLED
        assert manager.close(view).is_empty

    def test_every_published_result_carries_the_watermark_that_allowed_it(self) -> None:
        """The evidence for the claim. A result whose `closed_at` precedes its own interval end
        could not have come from this module, so a recording holding one is a recording of a
        bug — and that is checkable without re-running anything."""
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))
        result = manager.close(view_at("2026-03-14T09:40:00Z")).published[0]
        assert result.closed_at.epoch_millis >= result.interval_end.epoch_millis


class TestRestatement:
    def test_a_late_correction_restates_rather_than_overwrites(self) -> None:
        """Doctrine 4. The prior value, the cause and the delta all survive."""
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))
        manager.close(view_at("2026-03-14T09:40:00Z"))

        manager.admit(reading(340, "2026-03-17T06:00:00Z", source=Source.BATCH))
        restated = manager.close(view_at("2026-03-17T07:00:00Z")).restated[0]

        assert restated.revision == 1
        assert restated.energy_wh == 340
        assert restated.supersedes == 312
        assert restated.delta_wh == 28
        assert restated.restatement_cause is not None
        assert "batch" in restated.restatement_cause

    def test_a_late_duplicate_confirms_and_does_not_create_a_revision(self) -> None:
        """A restatement to the same number is a meaningless row in a settlement report, and it
        teaches whoever reads the report to skim revisions."""
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))
        manager.close(view_at("2026-03-14T09:40:00Z"))

        manager.admit(reading(312, "2026-03-17T06:00:00Z", source=Source.BATCH))
        emission = manager.close(view_at("2026-03-17T07:00:00Z"))

        assert emission.restated == ()
        assert [r.revision for r in emission.confirmed] == [0]
        assert emission.confirmed[0].duplicates_suppressed == 1

    def test_counts_accumulate_across_revisions(self) -> None:
        """A revision reporting one reading would make the earlier ones look discarded rather
        than superseded."""
        manager = WindowManager()
        manager.admit_all(
            [reading(312, "2026-03-14T09:17:00Z"), reading(312, "2026-03-14T09:18:00Z")]
        )
        manager.close(view_at("2026-03-14T09:40:00Z"))
        manager.admit(reading(340, "2026-03-17T06:00:00Z", source=Source.BATCH))
        restated = manager.close(view_at("2026-03-17T07:00:00Z")).restated[0]

        assert restated.readings == 3
        assert restated.duplicates_suppressed == 1
        assert restated.corrections_absorbed == 1

    def test_a_reading_past_the_allowance_is_refused_and_kept(self) -> None:
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))
        manager.close(view_at("2026-03-14T09:40:00Z"))

        refusal = manager.admit(reading(999, "2026-03-25T00:00:00Z", source=Source.BATCH))
        assert refusal is not None
        assert refusal.reason is Reason.TOO_LATE_FOR_WINDOW
        assert refusal.is_recoverable  # a real measurement, not rubbish

    def test_a_late_reading_for_a_window_that_never_published_is_accepted(self) -> None:
        """There is no settled number to protect. Refusing it would discard a real measurement
        to defend a total that was never stated."""
        manager = WindowManager(WindowPolicy(allowed_lateness=Duration.of_hours(1)))
        assert manager.admit(reading(312, "2026-03-25T00:00:00Z", source=Source.BATCH)) is None


class TestDeterminism:
    """Claim 2, at the level the arithmetic happens."""

    def test_shuffling_admissions_produces_identical_output(self) -> None:
        readings = [
            reading(312, "2026-03-14T09:17:00Z"),
            reading(312, "2026-03-14T09:19:00Z"),
            reading(500, "2026-03-14T09:18:00Z", meter="M2"),
            reading(700, "2026-03-14T09:18:00Z", meter="M3"),
            reading(150, "2026-03-14T09:33:00Z", interval=Instant.from_iso("2026-03-14T09:30:00Z")),
        ]
        view = view_at("2026-03-14T10:00:00Z")

        baseline = None
        for seed in range(25):
            shuffled = list(readings)
            random.Random(seed).shuffle(shuffled)
            manager = WindowManager()
            manager.admit_all(shuffled)
            emission = manager.close(view)
            if baseline is None:
                baseline = emission
            assert emission == baseline

    def test_results_are_emitted_in_content_order(self) -> None:
        manager = WindowManager()
        manager.admit_all(
            [
                reading(
                    1,
                    "2026-03-14T09:33:00Z",
                    meter="M9",
                    interval=Instant.from_iso("2026-03-14T09:30:00Z"),
                ),
                reading(2, "2026-03-14T09:17:00Z", meter="M2"),
                reading(3, "2026-03-14T09:17:00Z", meter="M1"),
            ]
        )
        published = manager.close(view_at("2026-03-14T10:00:00Z")).published
        assert [(r.interval_start.to_iso(), r.meter_id) for r in published] == [
            ("2026-03-14T09:15:00.000Z", "M1"),
            ("2026-03-14T09:15:00.000Z", "M2"),
            ("2026-03-14T09:30:00.000Z", "M9"),
        ]


class TestTheHoleInTheTotal:
    def test_a_result_computed_with_a_partition_excluded_says_so(self) -> None:
        """`ADVANCING_WITH_IDLE` on a settlement total is the difference between a number and a
        number with a substation missing from it."""
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))

        state = WatermarkState.declare(["S1", "S2"])
        state, _ = observe(
            state,
            [("S1", Instant.from_iso("2026-03-14T09:20:00Z"))],
            Instant.from_iso("2026-03-14T09:21:00Z"),
        )
        state, view = observe(
            state,
            [("S1", Instant.from_iso("2026-03-14T11:00:00Z"))],
            Instant.from_iso("2026-03-14T11:01:00Z"),
        )

        result = manager.close(view).published[0]
        assert result.watermark_status is WatermarkStatus.ADVANCING_WITH_IDLE
        assert result.idle_partitions == ("S2",)
