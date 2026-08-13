"""Every firmware shape's event time can actually be moved.

`capture.yml` compresses a four-day scenario into a twenty-minute window, and that only works if
the *event* times move with the arrivals: a correction that was three days late at source has to
arrive fifteen minutes late in the capture, or it lands past the four-day allowance and is
refused rather than absorbed.

The publisher used to do that with a regex over ISO-8601 text, and two of the three shapes in
the cast matched it. `fw1` writes epoch seconds as a bare integer. So a third of the fleet was
published with its event times untouched — five months before the capture window — and every
correction for one of those meters was refused `too_late_for_window`. 164 of them, in a run that
reported success, with the readings themselves published and the totals right.

**This test is written against the cast rather than against a list of shapes.** Add a firmware
generation whose timestamp nothing knows how to move and it fails here, on a laptop, instead of
in a capture that looks healthy.
"""

from __future__ import annotations

import pytest

from data import cast
from data.generate import INTERVALS_PER_DAY, energy_wh, generate, payload_with, retimed
from data.telemetry import readings
from watermark.core.normalise import DEFAULT_POLICY, normalise_meter_reading
from watermark.core.records import METER_INTERVAL, Source
from watermark.core.time import Duration, Instant

#: An instant with no relationship to the seeded day, so a payload that comes back unmoved is
#: unmistakable rather than plausibly correct.
MOVED_TO = Instant.from_iso("2031-07-04T12:34:56Z")

#: One meter per firmware generation in the cast, chosen by the cast rather than by hand.
BY_FIRMWARE = {meter.firmware: meter for meter in cast.METERS}


def test_the_cast_still_has_the_three_generations() -> None:
    """If this fails the fixture changed, and the cases below are testing less than they say."""
    assert set(BY_FIRMWARE) == {"fw1", "fw2", "fw3"}


@pytest.mark.parametrize("firmware", sorted(BY_FIRMWARE))
def test_every_firmware_shape_moves(firmware: str) -> None:
    meter = BY_FIRMWARE[firmware]
    original = Instant.from_iso("2026-03-14T10:00:00Z")
    raw = payload_with(meter, original, energy_wh(meter, 40))

    moved = retimed(raw, lambda _: MOVED_TO)

    assert moved != raw, (
        f"{firmware} came back byte-identical, so nothing in it was recognised as an instant. "
        "In a capture that means this generation publishes at its seeded date while the rest of "
        "the fleet publishes now, and every correction for it is refused as too late."
    )

    # And the core reads the new instant back. Rewriting the text is not the claim; rewriting it
    # into something `normalise` parses as the intended event time is.
    reading = normalise_meter_reading(moved, MOVED_TO, Source.STREAM, DEFAULT_POLICY)
    assert not hasattr(reading, "reason"), f"{firmware} no longer normalises after retiming"
    assert reading.event_time.epoch_millis == pytest.approx(
        MOVED_TO.epoch_millis, abs=Duration.of_seconds(1).millis
    ), (
        f"{firmware} moved, but not to where it was asked to. Within a second, because fw1 "
        "carries whole epoch seconds and cannot express less."
    )


@pytest.mark.parametrize("firmware", sorted(BY_FIRMWARE))
def test_retiming_preserves_the_offset_between_two_readings(firmware: str) -> None:
    """The transform is what matters, not one instant.

    A capture compresses; it does not translate. If two readings an hour apart come back an hour
    apart divided by the compression, the burst shape survives and so does the lateness the
    scenario is about.
    """
    meter = BY_FIRMWARE[firmware]
    first = Instant.from_iso("2026-03-14T10:00:00Z")
    second = first.plus(Duration.of_hours(1))
    compression = 60

    def compress(instant: Instant) -> Instant:
        return Instant(
            first.epoch_millis + (instant.epoch_millis - first.epoch_millis) // compression
        )

    moved_first = retimed(payload_with(meter, first, 100), compress)
    moved_second = retimed(payload_with(meter, second, 100), compress)

    read = [
        normalise_meter_reading(raw, second, Source.STREAM, DEFAULT_POLICY).event_time
        for raw in (moved_first, moved_second)
    ]
    gap = read[1].epoch_millis - read[0].epoch_millis
    assert gap == pytest.approx(Duration.of_minutes(1).millis, abs=Duration.of_seconds(1).millis)


def test_a_shifted_day_still_spans_many_windows() -> None:
    """The property a compressed day silently lost.

    A window is fifteen minutes of event time, fixed in the core. Dividing every event time by
    the same factor as the arrival pacing put a four-day span inside one or two grid cells, so
    the watermark never reached a window end and a live capture published nothing at all — 101
    evidence lines, every one a watermark status, and an empty lakehouse.

    Shifting keeps the grid. This asserts the day still covers most of its intervals after the
    move, which is the difference between a capture that closes windows and one that reports
    healthy watermarks over an empty table.
    """
    shift = Duration.of_days(30).millis
    starts = set()
    for delivery in generate():
        moved = retimed(delivery.raw, lambda i: Instant(i.epoch_millis + shift))
        reading = normalise_meter_reading(
            moved, Instant(delivery.ingest_time.epoch_millis + shift), Source.STREAM, DEFAULT_POLICY
        )
        if hasattr(reading, "reason"):
            continue
        starts.add(reading.event_time.epoch_millis // METER_INTERVAL.millis)

    assert len(starts) >= INTERVALS_PER_DAY - 2, (
        f"the day collapsed to {len(starts)} windows out of {INTERVALS_PER_DAY}. A capture over "
        "this stream would close almost nothing, and would look healthy while doing it."
    )


def test_a_telemetry_payload_is_moved_too() -> None:
    """The shape that was missed, and the reason this test exists at all.

    `data/telemetry.py` writes `event_time`, which is the name the record class uses and the
    right name for the field. No pattern in `_TIMESTAMPS` matched it, so `retimed` returned the
    payload unchanged — deliberately, because a firmware shape that grows a field must not crash
    the publisher — and every substation measurement arrived in the account stamped five months
    before the meter readings it was published alongside. Nothing raised. Nothing could.

    A capture would have shown telemetry landing, the decider reading it, and curtailment
    deciding, all correctly, on measurements from a different March.
    """
    sample = readings()[0]
    moved = retimed(sample.payload(), lambda instant: instant.plus(Duration.of_days(1)))
    assert moved != sample.payload()
    assert sample.event_time.plus(Duration.of_days(1)).to_iso() in moved


def test_every_published_shape_is_one_some_pattern_moves() -> None:
    """The general form of the check above, so the next new stream cannot slip through.

    Enumerated from what this repository actually publishes rather than from a list somebody
    maintains: the meter payloads the generator produces, and the telemetry payloads. A shape
    that no pattern moves is a stream that will arrive in the account carrying the cast's fixed
    date, and every downstream instant will be consistent, plausible and months wrong.
    """
    a_day = Duration.of_days(1)
    published = [delivery.raw for delivery in generate()] + [
        sample.payload() for sample in readings()
    ]
    unmoved = [raw for raw in published if retimed(raw, lambda i: i.plus(a_day)) == raw]
    assert not unmoved, (
        f"{len(unmoved)} published payload shapes are unmatched by every pattern in "
        f"`_TIMESTAMPS`, so they publish with the cast's fixed date. First: {unmoved[0][:120]}"
    )
