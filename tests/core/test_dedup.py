"""Collapsing retries and corrections — and doing it the same way whatever the order."""

from __future__ import annotations

import random

from watermark.core.dedup import collapse, group
from watermark.core.normalise import payload_hash
from watermark.core.records import MeterReading, Source
from watermark.core.time import Instant

START = Instant.from_iso("2026-03-14T09:15:00Z")


def reading(
    energy_wh: int,
    ingest: str,
    *,
    meter: str = "M1",
    firmware: str = "fw2",
    source: Source = Source.STREAM,
    event: str = "2026-03-14T09:16:00Z",
) -> MeterReading:
    return MeterReading(
        meter_id=meter,
        interval_start=START,
        event_time=Instant.from_iso(event),
        ingest_time=Instant.from_iso(ingest),
        energy_wh=energy_wh,
        firmware=firmware,
        source=source,
        payload_hash=payload_hash(meter, START, energy_wh),
    )


class TestRetries:
    def test_identical_readings_collapse_to_one(self) -> None:
        result = collapse(
            [reading(312, "2026-03-14T09:17:00Z"), reading(312, "2026-03-14T09:19:00Z")]
        )
        assert result.winner is not None
        assert result.winner.energy_wh == 312
        assert result.duplicates_suppressed == 1
        assert not result.was_corrected

    def test_the_earliest_ingested_copy_represents_them(self) -> None:
        """The copy that arrived soonest is the one whose lineage says when the system could
        first have acted on the measurement."""
        result = collapse(
            [reading(312, "2026-03-14T09:19:00Z"), reading(312, "2026-03-14T09:17:00Z")]
        )
        assert result.winner is not None
        assert result.winner.ingest_time == Instant.from_iso("2026-03-14T09:17:00Z")


class TestCorrections:
    def test_a_different_value_is_a_correction_not_a_duplicate(self) -> None:
        result = collapse(
            [reading(312, "2026-03-14T09:17:00Z"), reading(340, "2026-03-17T06:00:00Z")]
        )
        assert result.winner is not None
        assert result.winner.energy_wh == 340
        assert result.duplicates_suppressed == 0
        assert result.was_corrected

    def test_the_superseded_value_survives(self) -> None:
        """Doctrine 4: a correction never erases what was previously stated. The loser is
        returned so a restatement record can say what it replaced."""
        result = collapse(
            [reading(312, "2026-03-14T09:17:00Z"), reading(340, "2026-03-17T06:00:00Z")]
        )
        assert [r.energy_wh for r in result.superseded] == [312]

    def test_the_latest_statement_wins_however_it_is_ordered(self) -> None:
        bag = [
            reading(300, "2026-03-14T09:17:00Z"),
            reading(340, "2026-03-17T06:00:00Z"),
            reading(320, "2026-03-15T06:00:00Z"),
        ]
        for _ in range(20):
            random.shuffle(bag)
            winner = collapse(bag).winner
            assert winner is not None and winner.energy_wh == 340


class TestOrderIndependence:
    """Claim 2's foundation. 'First one wins' would make the output depend on partitioning,
    retry timing and how the replay happened to be shuffled — identical in energy, different
    in lineage, and no total would reveal it."""

    def test_shuffling_and_duplicating_does_not_change_the_answer(self) -> None:
        bag = [
            reading(312, "2026-03-14T09:17:00Z", firmware="fw1"),
            reading(312, "2026-03-14T09:19:00Z", firmware="fw3"),
            reading(312, "2026-03-14T09:18:00Z", source=Source.BATCH),
            reading(340, "2026-03-17T06:00:00Z", source=Source.BATCH),
        ]
        expected = collapse(bag)
        for seed in range(50):
            shuffled = list(bag)
            random.Random(seed).shuffle(shuffled)
            assert collapse(shuffled) == expected

    def test_copies_ingested_in_the_same_millisecond_still_have_one_winner(self) -> None:
        """The tie-break exists so the order is *total*. Without it the shuffle test fails
        intermittently, which is the worst way for it to fail."""
        same_instant = [
            reading(312, "2026-03-14T09:17:00Z", firmware="fw3"),
            reading(312, "2026-03-14T09:17:00Z", firmware="fw1"),
        ]
        winner = collapse(same_instant).winner
        assert winner is not None and winner.firmware == "fw1"
        assert collapse(list(reversed(same_instant))).winner == winner


class TestEdges:
    def test_an_empty_bag_has_no_winner_and_that_is_a_fact(self) -> None:
        """A closed window with no readings is an absence, and claim 1 cares about absences."""
        result = collapse([])
        assert result.winner is None
        assert result.duplicates_suppressed == 0

    def test_grouping_keys_on_the_meter_and_the_interval(self) -> None:
        buckets = group(
            [reading(1, "2026-03-14T09:17:00Z"), reading(2, "2026-03-14T09:17:00Z", meter="M2")]
        )
        assert sorted(buckets) == [("M1", START.epoch_millis), ("M2", START.epoch_millis)]
