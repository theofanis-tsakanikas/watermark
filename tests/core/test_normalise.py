"""Three firmware generations, one reading — and every way that can go wrong."""

from __future__ import annotations

import json

import pytest

from watermark.core.normalise import (
    DEFAULT_POLICY,
    NormalisationPolicy,
    normalise_meter_reading,
    payload_hash,
)
from watermark.core.quarantine import Quarantined, Reason
from watermark.core.records import MeterReading, Source
from watermark.core.time import Duration, Instant

INGEST = Instant.from_iso("2026-03-14T09:31:00Z")

FW1 = '{"v":1,"mid":"M000123","ts":1773479700,"wh":312}'
FW2 = (
    '{"schema":"2","meter_id":"M000123","timestamp":"2026-03-14T09:15:00Z",'
    '"energy":{"value":0.312,"unit":"kWh"}}'
)
FW3 = (
    '{"schemaVersion":"3.0","meter":{"id":"M000123"},'
    '"interval":{"start":"2026-03-14T09:15:00.000Z"},"energy":{"value":312,"unit":"Wh"}}'
)


def _reading(raw: str, ingest: Instant = INGEST) -> MeterReading:
    result = normalise_meter_reading(raw, ingest, Source.STREAM)
    assert isinstance(result, MeterReading), result
    return result


def _refusal(raw: str, ingest: Instant = INGEST) -> Quarantined:
    result = normalise_meter_reading(raw, ingest, Source.STREAM)
    assert isinstance(result, Quarantined), result
    return result


class TestTheThreeShapesConverge:
    @pytest.mark.parametrize("raw", [FW1, FW2, FW3])
    def test_each_shape_yields_the_same_reading(self, raw: str) -> None:
        reading = _reading(raw)
        assert reading.meter_id == "M000123"
        assert reading.energy_wh == 312
        assert reading.interval_start.to_iso() == "2026-03-14T09:15:00.000Z"

    def test_the_payload_hash_is_the_same_across_firmware(self) -> None:
        """The property the deduplication key rests on.

        A meter upgraded between two retries sends the same measurement in a different shape.
        If the hash saw the shape, the duplicate would survive into a settlement total.
        """
        hashes = {_reading(raw).payload_hash for raw in (FW1, FW2, FW3)}
        assert len(hashes) == 1

    def test_the_firmware_is_still_recorded(self) -> None:
        """Normalised away for the arithmetic, kept for the fleet. 'Which firmware duplicates?'
        is answerable only if the answer was written down at the only moment it was known."""
        assert {_reading(raw).firmware for raw in (FW1, FW2, FW3)} == {"fw1", "fw2", "fw3"}


class TestUnits:
    def test_kilowatt_hours_convert_exactly(self) -> None:
        assert _reading(FW2).energy_wh == 312

    def test_a_value_binary_floating_point_gets_wrong(self) -> None:
        """1.001 kWh is 1001 Wh. Through a float it is 1000.9999999999999, and every way of
        making that an integer — truncate, floor, `int()` — is off by one watt-hour, in one
        direction, on every reading of that shape across a fleet of 250,000 meters.

        The first line is the demonstration that the hazard is real rather than folklore; the
        second is this repository not falling into it. Parsing JSON numbers as `Decimal` is the
        whole of the defence, and it has to happen inside `json.loads` — once a float exists,
        the exact decimal the device sent is already gone.
        """
        assert int(json.loads('{"x":1.001}')["x"] * 1000) == 1000  # the hazard

        raw = (
            '{"schema":"2","meter_id":"M1","timestamp":"2026-03-14T09:15:00Z",'
            '"energy":{"value":1.001,"unit":"kWh"}}'
        )
        assert _reading(raw).energy_wh == 1001  # what we do instead

    def test_megawatt_hours_convert_exactly(self) -> None:
        raw = (
            '{"schema":"2","meter_id":"M1","timestamp":"2026-03-14T09:15:00Z",'
            '"energy":{"value":0.0025,"unit":"MWh"}}'
        )
        assert _reading(raw).energy_wh == 2500

    def test_an_unknown_unit_is_refused_not_assumed(self) -> None:
        """Defaulting to kWh would be wrong by a factor of a thousand, consistently, in every
        total — so nothing downstream would look inconsistent."""
        raw = (
            '{"schema":"2","meter_id":"M1","timestamp":"2026-03-14T09:15:00Z",'
            '"energy":{"value":1,"unit":"BTU"}}'
        )
        assert _refusal(raw).reason is Reason.UNKNOWN_UNIT

    def test_precision_finer_than_a_watt_hour_is_refused_not_rounded(self) -> None:
        """0.3125 kWh is 312.5 Wh. Rounding loses energy a settlement total is supposed to
        balance, and it loses it silently and in one direction."""
        raw = (
            '{"schema":"2","meter_id":"M1","timestamp":"2026-03-14T09:15:00Z",'
            '"energy":{"value":0.3125,"unit":"kWh"}}'
        )
        refusal = _refusal(raw)
        assert refusal.reason is Reason.PRECISION_BEYOND_CANONICAL_UNIT
        assert "312.5" in refusal.detail


class TestClockSkew:
    def test_a_device_clock_slightly_ahead_is_accepted(self) -> None:
        """Some skew is ordinary — network delay, batching, an NTP correction in flight."""
        ingest = Instant.from_iso("2026-03-14T09:13:00Z")  # two minutes before event time
        assert _reading(FW2, ingest).energy_wh == 312

    def test_a_device_far_in_the_future_is_quarantined_with_the_reason(self) -> None:
        """Quarantined rather than clamped. Clamping puts a real measurement in the wrong
        interval and leaves nothing anywhere to notice."""
        ingest = Instant.from_iso("2026-03-14T08:00:00Z")  # event time is 75 minutes ahead
        refusal = _refusal(FW2, ingest)
        assert refusal.reason is Reason.CLOCK_SKEW_FUTURE
        assert refusal.disposition.value == "terminal"

    def test_the_tolerance_is_policy_not_arithmetic(self) -> None:
        tight = NormalisationPolicy(skew_tolerance=Duration.of_seconds(1), max_energy_wh=10**7)
        ingest = Instant.from_iso("2026-03-14T09:14:00Z")
        assert isinstance(normalise_meter_reading(FW2, ingest, Source.STREAM), MeterReading)
        assert isinstance(normalise_meter_reading(FW2, ingest, Source.STREAM, tight), Quarantined)


class TestRefusals:
    def test_an_unknown_shape_is_named_not_guessed(self) -> None:
        refusal = _refusal('{"meterId":"M1","kwh":1}')
        assert refusal.reason is Reason.UNKNOWN_PAYLOAD_SHAPE

    def test_a_naive_timestamp_arrives_here_rather_than_being_assumed_utc(self) -> None:
        raw = (
            '{"schema":"2","meter_id":"M1","timestamp":"2026-03-14T09:15:00",'
            '"energy":{"value":1,"unit":"Wh"}}'
        )
        refusal = _refusal(raw)
        assert refusal.reason is Reason.MALFORMED_FIELD
        assert "offset" in refusal.detail

    def test_negative_energy_is_implausible_for_a_meter_that_cannot_export(self) -> None:
        raw = (
            '{"schema":"2","meter_id":"M1","timestamp":"2026-03-14T09:15:00Z",'
            '"energy":{"value":-5,"unit":"Wh"}}'
        )
        assert _refusal(raw).reason is Reason.IMPLAUSIBLE_VALUE

    def test_energy_past_the_fleet_ceiling_is_implausible(self) -> None:
        raw = (
            '{"schema":"2","meter_id":"M1","timestamp":"2026-03-14T09:15:00Z",'
            f'"energy":{{"value":{DEFAULT_POLICY.max_energy_wh + 1},"unit":"Wh"}}}}'
        )
        assert _refusal(raw).reason is Reason.IMPLAUSIBLE_VALUE

    def test_a_missing_meter_id_is_malformed(self) -> None:
        assert _refusal('{"v":1,"ts":1773479700,"wh":1}').reason is Reason.MALFORMED_FIELD

    @pytest.mark.parametrize("raw", ["not json at all", "[1,2,3]", "null"])
    def test_nothing_raises_whatever_arrives(self, raw: str) -> None:
        """The function is total. A record that blows up inside a stream operator is a record
        whose reason nobody can count."""
        assert isinstance(normalise_meter_reading(raw, INGEST, Source.STREAM), Quarantined)

    def test_the_raw_payload_survives_into_the_quarantine(self) -> None:
        """A quarantine queue holding a half-normalised record is a queue nobody can
        reprocess, because normalisation is the thing under suspicion."""
        raw = '{"v":1,"mid":"M1","ts":1773479700,"wh":-1}'
        assert _refusal(raw).payload == raw


class TestThePayloadHash:
    def test_energy_is_in_the_hash_so_a_correction_survives(self) -> None:
        """A retry collapses; a different value for the same interval is a restatement and has
        to survive in order to become one."""
        start = Instant.from_iso("2026-03-14T09:15:00Z")
        assert payload_hash("M1", start, 312) != payload_hash("M1", start, 340)

    def test_event_time_is_not_in_the_hash_so_a_drifted_retry_still_collapses(self) -> None:
        """A retrying meter resends the same measurement, and its clock may have been corrected
        in between. Hashing the event time would make the two records different, and the
        duplicate would reach a settlement total."""
        earlier = normalise_meter_reading(
            '{"schema":"2","meter_id":"M1","timestamp":"2026-03-14T09:16:00Z",'
            '"energy":{"value":312,"unit":"Wh"}}',
            INGEST,
            Source.STREAM,
        )
        later = normalise_meter_reading(
            '{"schema":"2","meter_id":"M1","timestamp":"2026-03-14T09:19:30Z",'
            '"energy":{"value":312,"unit":"Wh"}}',
            INGEST,
            Source.STREAM,
        )
        assert isinstance(earlier, MeterReading) and isinstance(later, MeterReading)
        assert earlier.payload_hash == later.payload_hash
        assert earlier.event_time != later.event_time
