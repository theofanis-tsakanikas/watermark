"""Lineage ids are derived, so a replay produces the same ones."""

from __future__ import annotations

import random

from watermark.core.normalise import payload_hash
from watermark.core.records import MeterReading, Source
from watermark.core.time import Instant
from watermark.core.watermarks import WatermarkState, observe
from watermark.core.windows import WindowManager
from watermark.lineage import derive, of_reading, of_result
from watermark.lineage.restatement import restatements_for, total_delta_wh

START = Instant.from_iso("2026-03-14T09:15:00Z")


def reading(
    energy_wh: int, ingest: str, *, meter: str = "M1", source: Source = Source.STREAM
) -> MeterReading:
    return MeterReading(
        meter_id=meter,
        interval_start=START,
        event_time=START,
        ingest_time=Instant.from_iso(ingest),
        energy_wh=energy_wh,
        firmware="fw2",
        source=source,
        payload_hash=payload_hash(meter, START, energy_wh),
    )


def view_at(moment: str):
    _, view = observe(WatermarkState.declare(["S1"]), [("S1", Instant.from_iso(moment))])
    return view


class TestDerivedIdentity:
    def test_the_same_record_always_yields_the_same_id(self) -> None:
        """`uuid4()` here is the obvious implementation and it ends claim 2 on the first
        replay: the same events, different ids, identical numbers, different bytes."""
        assert of_reading(reading(312, "2026-03-14T09:17:00Z")) == of_reading(
            reading(312, "2026-03-14T09:17:00Z")
        )

    def test_two_copies_of_one_measurement_have_different_lineage(self) -> None:
        """The payload hash answers 'is this the same measurement?'. The lineage id answers 'is
        this the same record?'. Deduplication needs the first; tracing a number back to the
        delivery it arrived in needs the second."""
        first = reading(312, "2026-03-14T09:17:00Z")
        retry = reading(312, "2026-03-14T09:19:00Z")
        assert first.payload_hash == retry.payload_hash
        assert of_reading(first) != of_reading(retry)

    def test_a_derived_id_does_not_depend_on_parent_order(self) -> None:
        """Otherwise the id of a total depends on the order its readings arrived in — the exact
        failure claim 2 exists to catch, reappearing inside the mechanism meant to prove it did
        not happen."""
        parents = [of_reading(reading(n, "2026-03-14T09:17:00Z")) for n in (1, 2, 3, 4)]
        expected = derive("window", "k", parents)
        for seed in range(20):
            shuffled = list(parents)
            random.Random(seed).shuffle(shuffled)
            assert derive("window", "k", shuffled) == expected

    def test_the_kind_separates_otherwise_identical_material(self) -> None:
        parents = [of_reading(reading(1, "2026-03-14T09:17:00Z"))]
        assert derive("window", "k", parents) != derive("settlement", "k", parents)

    def test_a_revision_has_its_own_identity(self) -> None:
        """Otherwise revision 0 and revision 1 are indistinguishable to anything holding a
        reference, and 'which version of this total was I told?' has no answer."""
        manager = WindowManager()
        manager.admit(reading(312, "2026-03-14T09:17:00Z"))
        first = manager.close(view_at("2026-03-14T09:40:00Z")).published[0]
        manager.admit(reading(340, "2026-03-17T06:00:00Z", source=Source.BATCH))
        second = manager.close(view_at("2026-03-17T07:00:00Z")).restated[0]
        assert of_result(first, ()) != of_result(second, ())


class TestRestatementRecords:
    def _restated(self):
        manager = WindowManager()
        original = reading(312, "2026-03-14T09:17:00Z")
        manager.admit(original)
        published = manager.close(view_at("2026-03-14T09:40:00Z")).published[0]

        correction = reading(340, "2026-03-17T06:00:00Z", source=Source.BATCH)
        manager.admit(correction)
        emission = manager.close(view_at("2026-03-17T07:00:00Z"))

        key = ("M1", START.epoch_millis)
        return restatements_for(
            emission,
            {key: (of_reading(original), of_reading(correction))},
            {key: of_result(published, (of_reading(original),))},
        )

    def test_the_prior_value_survives_in_the_record(self) -> None:
        restatement = self._restated()[0]
        assert restatement.previous_energy_wh == 312
        assert restatement.new_energy_wh == 340

    def test_the_delta_is_signed_because_a_refund_is_not_a_charge(self) -> None:
        assert self._restated()[0].delta_wh == 28

    def test_it_points_at_what_it_supersedes(self) -> None:
        restatement = self._restated()[0]
        assert restatement.supersedes_lineage_id is not None
        assert restatement.supersedes_lineage_id != restatement.lineage_id

    def test_the_row_is_flat_and_explicit(self) -> None:
        """Explicit rather than `asdict`, so a new field fails the seed check instead of
        silently changing the bytes of every committed recording."""
        row = self._restated()[0].as_row()
        assert row["previous_energy_wh"] == 312
        assert row["interval_start"] == "2026-03-14T09:15:00.000Z"

    def test_the_net_movement_is_available_without_arithmetic(self) -> None:
        assert total_delta_wh(self._restated()) == 28
