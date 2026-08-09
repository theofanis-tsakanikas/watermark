"""Rolling windows into invoiced hours, and what an invoice says when a number moves."""

from __future__ import annotations

from watermark.core.normalise import payload_hash
from watermark.core.records import MeterReading, Source
from watermark.core.time import Instant
from watermark.core.watermarks import WatermarkStatus
from watermark.core.windows import WindowResult
from watermark.lineage.identity import of_reading
from watermark.settlement import compare, net_delta_wh, settle, settle_groups

HOUR = Instant.from_iso("2026-03-14T09:00:00Z")


def window(minute: int, energy_wh: int, *, meter: str = "M1", revision: int = 0) -> WindowResult:
    start = Instant.from_iso(f"2026-03-14T09:{minute:02d}:00Z")
    return WindowResult(
        meter_id=meter,
        interval_start=start,
        energy_wh=energy_wh,
        readings=1,
        duplicates_suppressed=0,
        corrections_absorbed=0,
        closed_at=start.plus(Instant(0).since(Instant(-3_600_000))),
        watermark_status=WatermarkStatus.ADVANCING,
        idle_partitions=(),
        first_seen_at=start,
        revision=revision,
        supersedes=None if revision == 0 else energy_wh - 10,
    )


FULL_HOUR = [window(0, 100), window(15, 110), window(30, 120), window(45, 130)]


class TestHourlyTotals:
    def test_four_intervals_make_a_complete_hour(self) -> None:
        total = settle(FULL_HOUR)[0]
        assert total.energy_wh == 460
        assert total.intervals == 4
        assert total.is_complete

    def test_a_missing_interval_is_reported_not_absorbed(self) -> None:
        """An hour built from three intervals is not a smaller total, it is a different kind of
        statement — and an invoice that cannot tell them apart is one nobody can defend."""
        total = settle(FULL_HOUR[:3])[0]
        assert total.energy_wh == 330
        assert not total.is_complete

    def test_a_restatement_supersedes_rather_than_adding(self) -> None:
        """Summing a window and its revision double-counts the interval — the single
        arithmetic mistake this whole path exists to avoid."""
        total = settle([*FULL_HOUR, window(0, 140, revision=1)])[0]
        assert total.energy_wh == 500  # 140 + 110 + 120 + 130, not 600
        assert total.revision == 1

    def test_the_idle_hole_travels_to_the_invoice(self) -> None:
        incomplete = WindowResult(
            **{
                **{
                    field: getattr(FULL_HOUR[0], field)
                    for field in FULL_HOUR[0].__slots__
                    if field != "idle_partitions"
                },
                "idle_partitions": ("SUB-03",),
            }
        )
        assert settle([incomplete, *FULL_HOUR[1:]])[0].computed_with_idle_partition

    def test_totals_are_emitted_in_content_order(self) -> None:
        totals = settle([window(0, 1, meter="M9"), window(0, 1, meter="M1")])
        assert [total.meter_id for total in totals] == ["M1", "M9"]

    def test_the_lineage_is_derived_from_the_contributing_windows(self) -> None:
        parents = {
            (w.meter_id, w.interval_start.epoch_millis, w.revision): of_reading(
                MeterReading(
                    "M1",
                    w.interval_start,
                    w.interval_start,
                    w.interval_start,
                    w.energy_wh,
                    "fw2",
                    Source.STREAM,
                    payload_hash("M1", w.interval_start, w.energy_wh),
                )
            )
            for w in FULL_HOUR
        }
        assert settle(FULL_HOUR, parents)[0].lineage_id != settle(FULL_HOUR)[0].lineage_id


class TestBalancingGroups:
    def test_meters_sum_into_their_group(self) -> None:
        hours = settle([*FULL_HOUR, *[window(m, 10, meter="M2") for m in (0, 15, 30, 45)]])
        group = settle_groups(hours, {"M1": "BG-1", "M2": "BG-1"})[0]
        assert group.energy_wh == 500
        assert group.meters == 2

    def test_an_incomplete_meter_is_named_not_counted(self) -> None:
        """The question after a short total is always *which* meters."""
        hours = settle([*FULL_HOUR[:3], *[window(m, 10, meter="M2") for m in (0, 15, 30, 45)]])
        group = settle_groups(hours, {"M1": "BG-1", "M2": "BG-1"})[0]
        assert group.incomplete_meters == ("M1",)
        assert not group.is_complete

    def test_a_meter_with_no_membership_is_excluded_rather_than_defaulted(self) -> None:
        """An unattributed megawatt-hour is a market position somebody did not take. Bucketing
        it into a default group would put it on a stranger's books."""
        groups = settle_groups(settle(FULL_HOUR), {})
        assert groups == ()


class TestInvoiceGrainRestatement:
    def test_an_hour_that_moved_states_what_it_was(self) -> None:
        before = settle(FULL_HOUR)
        after = settle([*FULL_HOUR, window(0, 140, revision=1)])
        moved = compare(before, after)[0]
        assert moved.previous_energy_wh == 460
        assert moved.new_energy_wh == 500
        assert moved.delta_wh == 40
        assert net_delta_wh(compare(before, after)) == 40

    def test_an_hour_that_did_not_move_produces_no_row(self) -> None:
        assert compare(settle(FULL_HOUR), settle(FULL_HOUR)) == ()

    def test_a_new_hour_is_not_a_restatement(self) -> None:
        """Nothing was previously stated about it. A row reading 'was 0, now 460' would go in
        front of a customer who was never invoiced for the zero."""
        assert compare([], settle(FULL_HOUR)) == ()

    def test_an_hour_still_short_after_moving_says_so(self) -> None:
        """It will move again, and saying so is the difference between a correction and a
        correction somebody has to chase."""
        before = settle(FULL_HOUR[:3])
        after = settle([*FULL_HOUR[:3], window(0, 140, revision=1)])
        assert not compare(before, after)[0].now_complete
