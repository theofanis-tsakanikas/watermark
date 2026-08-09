"""Three firmware generations, one reading.

`docs/SCENARIO.md` puts three payload shapes on the wire at once, which is what a real meter
fleet looks like: replacing 250,000 devices takes years, so the old ones keep reporting in the
old shape for as long as they last. Normalisation is the first thing that touches a record and
the only thing that knows the difference. Everything downstream sees `MeterReading`.

Doing it here rather than later is not tidiness. A windowing function that has to ask which
firmware wrote a record has three code paths, and two of them are exercised by whichever
firmware happens to dominate the test fixtures.

Three decisions are load-bearing.

**JSON numbers are parsed as `Decimal`, never as `float`.** Firmware 2 reports kilowatt-hours,
and `0.312 * 1000` in binary floating point is `311.99999999999994`. Converting that to an
integer number of watt-hours by any route — truncation, rounding, `int()` — is a decision about
somebody's electricity bill made by IEEE 754. `Decimal` multiplies exactly, and a value that
does not land on a whole watt-hour is quarantined rather than rounded.

**An unknown unit is refused, never assumed.** The temptation is to default to kWh. The cost of
being wrong is a factor of a thousand in a settlement total, and it is a factor of a thousand
that no downstream check would catch, because every total would be consistently wrong.

**The function is total.** A payload either becomes a reading or becomes a `Quarantined` with a
reason from the closed vocabulary. It never raises, because a record that blows up in a stream
operator is a record whose reason nobody can count.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from watermark.core.quarantine import Quarantined, Reason
from watermark.core.records import METER_INTERVAL, MeterReading, Source
from watermark.core.time import Duration, EventTimeError, Instant

#: Multipliers into the canonical unit, watt-hours. Exact integers, so the conversion is exact.
_UNITS: Final[dict[str, int]] = {
    "Wh": 1,
    "kWh": 1_000,
    "MWh": 1_000_000,
}

#: How much of the hash to keep. 128 bits is far past any collision this fleet could produce —
#: 250,000 meters at 96 intervals a day — and a full 64-character digest in every golden
#: recording makes the recordings unreadable, which makes them unreviewed.
_HASH_LENGTH: Final = 32


@dataclass(frozen=True, slots=True)
class NormalisationPolicy:
    """The thresholds normalisation applies, passed in rather than hardcoded.

    They are policy, not arithmetic: a fleet with different meters has different plausible
    ceilings, and a fleet with better time sync has a tighter skew tolerance. In phase 2 these
    arrive from a contract. Until then the default below is the single place they are stated.
    """

    #: How far ahead of ingestion a device's clock may claim to be before the reading is
    #: quarantined. Some skew is ordinary — network delay, batching, an NTP correction in
    #: flight. Beyond this it is not skew, it is a wrong clock.
    skew_tolerance: Duration
    #: The largest interval energy any meter in this fleet could genuinely record. A commercial
    #: meter on a 15-minute interval tops out far below this; the ceiling exists to catch a
    #: unit mistake or a corrupt field, not to second-guess a large customer.
    max_energy_wh: int


DEFAULT_POLICY: Final = NormalisationPolicy(
    skew_tolerance=Duration.of_minutes(5),
    max_energy_wh=10_000_000,
)

#: What a normaliser returns. A union rather than an exception, so a caller must handle both.
Normalised = MeterReading | Quarantined


def normalise_meter_reading(
    raw: str,
    ingest_time: Instant,
    source: Source,
    policy: NormalisationPolicy = DEFAULT_POLICY,
) -> Normalised:
    """Turn one raw meter payload into a reading, or say why it could not be one.

    `ingest_time` is supplied by the caller and never read from a clock — that is what makes
    this function replayable, and `scripts/check_core_is_pure.py` enforces it.
    """
    try:
        # `parse_float=Decimal` is the whole of the floating-point defence, and it has to be
        # here: once `json` has produced a float the exact decimal the device sent is already
        # gone, and no amount of care downstream recovers it.
        payload = json.loads(raw, parse_float=Decimal)
    except (json.JSONDecodeError, ValueError) as exc:
        return Quarantined(Reason.UNKNOWN_PAYLOAD_SHAPE, f"not JSON: {exc}", raw)

    if not isinstance(payload, dict):
        return Quarantined(
            Reason.UNKNOWN_PAYLOAD_SHAPE,
            f"a meter payload is an object, not {type(payload).__name__}",
            raw,
        )

    extract = _SHAPES.get(_discriminate(payload))
    if extract is None:
        return Quarantined(
            Reason.UNKNOWN_PAYLOAD_SHAPE,
            f"no firmware shape matches keys {sorted(payload)}",
            raw,
        )

    extracted = extract(payload, raw)
    if isinstance(extracted, Quarantined):
        return extracted

    return _assemble(extracted, raw, ingest_time, source, policy)


# ── Shape detection and extraction ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Extracted:
    """What every firmware shape must yield before the common rules run.

    Extraction knows about firmware; everything after it does not. Keeping the split explicit
    is what stops a plausibility rule being written once per shape and drifting twice.
    """

    firmware: str
    meter_id: str
    event_time: Instant
    energy_wh: int


def _discriminate(payload: dict[str, object]) -> str:
    """Which firmware wrote this, by the key only that firmware uses.

    Keyed on a discriminator rather than guessed from the shape. Guessing works until two
    generations overlap in their fields, at which point it silently picks one.
    """
    for key, firmware in (("v", "fw1"), ("schema", "fw2"), ("schemaVersion", "fw3")):
        if key in payload:
            return firmware
    return ""


def _extract_fw1(payload: dict[str, object], raw: str) -> _Extracted | Quarantined:
    """The oldest generation: flat, epoch seconds, energy already in watt-hours."""
    meter_id = payload.get("mid")
    seconds = payload.get("ts")
    energy = payload.get("wh")

    if not isinstance(meter_id, str) or not meter_id:
        return Quarantined(Reason.MALFORMED_FIELD, f"mid is {meter_id!r}", raw)
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        return Quarantined(Reason.MALFORMED_FIELD, f"ts is {seconds!r}, want epoch seconds", raw)
    converted = _to_watt_hours(energy, "Wh", raw)
    if isinstance(converted, Quarantined):
        return converted

    return _Extracted("fw1", meter_id, Instant.from_epoch_millis(seconds * 1000), converted)


def _extract_fw2(payload: dict[str, object], raw: str) -> _Extracted | Quarantined:
    """The middle generation: ISO-8601 timestamps and a unit-tagged energy value."""
    meter_id = payload.get("meter_id")
    timestamp = payload.get("timestamp")
    energy = payload.get("energy")

    if not isinstance(meter_id, str) or not meter_id:
        return Quarantined(Reason.MALFORMED_FIELD, f"meter_id is {meter_id!r}", raw)
    if not isinstance(energy, dict):
        return Quarantined(Reason.MALFORMED_FIELD, f"energy is {energy!r}, want an object", raw)

    moment = _to_instant(timestamp, raw)
    if isinstance(moment, Quarantined):
        return moment
    converted = _to_watt_hours(energy.get("value"), energy.get("unit"), raw)
    if isinstance(converted, Quarantined):
        return converted

    return _Extracted("fw2", meter_id, moment, converted)


def _extract_fw3(payload: dict[str, object], raw: str) -> _Extracted | Quarantined:
    """The newest generation: nested, and it names the interval start rather than a reading
    instant. Both still go through the same flooring below — trusting the device to have
    aligned it would make the boundary a property of the firmware."""
    meter = payload.get("meter")
    interval = payload.get("interval")
    energy = payload.get("energy")

    nested = (meter, interval, energy)
    if not all(isinstance(part, dict) for part in nested):
        return Quarantined(
            Reason.MALFORMED_FIELD,
            "expected nested meter, interval and energy objects",
            raw,
        )

    meter_id = meter.get("id")
    if not isinstance(meter_id, str) or not meter_id:
        return Quarantined(Reason.MALFORMED_FIELD, f"meter.id is {meter_id!r}", raw)

    moment = _to_instant(interval.get("start"), raw)
    if isinstance(moment, Quarantined):
        return moment
    converted = _to_watt_hours(energy.get("value"), energy.get("unit"), raw)
    if isinstance(converted, Quarantined):
        return converted

    return _Extracted("fw3", meter_id, moment, converted)


_SHAPES: Final = {
    "fw1": _extract_fw1,
    "fw2": _extract_fw2,
    "fw3": _extract_fw3,
}


# ── The rules every shape shares ─────────────────────────────────────────────


def _to_instant(value: object, raw: str) -> Instant | Quarantined:
    if not isinstance(value, str):
        return Quarantined(Reason.MALFORMED_FIELD, f"timestamp is {value!r}, want a string", raw)
    try:
        return Instant.from_iso(value)
    except EventTimeError as exc:
        # A naive timestamp lands here, and that is the intended route: `Instant.from_iso`
        # refuses to guess a device's timezone, so the record is quarantined with a reason
        # instead of silently landing in the wrong interval.
        return Quarantined(Reason.MALFORMED_FIELD, str(exc), raw)


def _to_watt_hours(value: object, unit: object, raw: str) -> int | Quarantined:
    """Convert a unit-tagged quantity into an exact integer number of watt-hours."""
    if not isinstance(unit, str):
        return Quarantined(Reason.UNKNOWN_UNIT, f"unit is {unit!r}", raw)
    multiplier = _UNITS.get(unit)
    if multiplier is None:
        return Quarantined(
            Reason.UNKNOWN_UNIT,
            f"unit {unit!r} is not one of {sorted(_UNITS)}; it is not assumed to be kWh, "
            "because being wrong about that is a factor of a thousand in a settlement total "
            "and every total would be consistently wrong",
            raw,
        )

    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return Quarantined(Reason.MALFORMED_FIELD, f"energy value is {value!r}", raw)

    try:
        exact = Decimal(value) * multiplier
    except InvalidOperation as exc:  # pragma: no cover — Decimal from JSON is always finite
        return Quarantined(Reason.MALFORMED_FIELD, f"energy value is unusable: {exc}", raw)

    whole = exact.to_integral_value()
    if exact != whole:
        return Quarantined(
            Reason.PRECISION_BEYOND_CANONICAL_UNIT,
            f"{value} {unit} is {exact} Wh, which is not a whole watt-hour; it is refused "
            "rather than rounded, because rounding here loses energy a settlement total is "
            "supposed to balance",
            raw,
        )
    return int(whole)


def _assemble(
    extracted: _Extracted,
    raw: str,
    ingest_time: Instant,
    source: Source,
    policy: NormalisationPolicy,
) -> Normalised:
    if extracted.energy_wh < 0:
        return Quarantined(
            Reason.IMPLAUSIBLE_VALUE,
            f"{extracted.energy_wh} Wh: these meters do not export",
            raw,
        )
    if extracted.energy_wh > policy.max_energy_wh:
        return Quarantined(
            Reason.IMPLAUSIBLE_VALUE,
            f"{extracted.energy_wh} Wh in one interval exceeds the fleet ceiling of "
            f"{policy.max_energy_wh} Wh",
            raw,
        )

    skew = extracted.event_time.since(ingest_time)
    if skew.millis > policy.skew_tolerance.millis:
        return Quarantined(
            Reason.CLOCK_SKEW_FUTURE,
            f"event time {extracted.event_time} is {skew} ahead of ingestion "
            f"{ingest_time}, beyond the {policy.skew_tolerance} tolerance; it is quarantined "
            "rather than clamped, because clamping puts a real measurement in the wrong "
            "interval and leaves nothing to notice",
            raw,
        )

    interval_start = extracted.event_time.floor_to(METER_INTERVAL)
    return MeterReading(
        meter_id=extracted.meter_id,
        interval_start=interval_start,
        event_time=extracted.event_time,
        ingest_time=ingest_time,
        energy_wh=extracted.energy_wh,
        firmware=extracted.firmware,
        source=source,
        payload_hash=payload_hash(extracted.meter_id, interval_start, extracted.energy_wh),
    )


def payload_hash(meter_id: str, interval_start: Instant, energy_wh: int) -> str:
    """The content hash that, with the meter and the interval, forms the deduplication key.

    **Event time is deliberately not in it.** A retrying meter resends the same measurement,
    and its clock may have been corrected between attempts; hashing the event time would make
    those two records different and the duplicate would survive into a settlement total.

    **Firmware is deliberately not in it either.** The same reading re-sent by an upgraded
    device is the same reading.

    **Energy is in it**, and that is what separates a retry from a correction. A meter
    resending an identical value collapses; a meter sending a *different* value for an
    interval already published is a restatement, and it has to survive in order to be one
    (doctrine 4).

    The hash exists rather than using the three fields directly so that the key stays
    fixed-width, and so that widening what identifies a reading changes the hash — visibly, in
    every recording — instead of quietly changing the meaning of a key nothing re-derives.
    """
    material = f"{meter_id}|{interval_start.to_iso()}|{energy_wh}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:_HASH_LENGTH]
