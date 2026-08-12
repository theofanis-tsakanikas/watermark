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
from data.generate import energy_wh, payload_with, retimed
from watermark.core.normalise import DEFAULT_POLICY, normalise_meter_reading
from watermark.core.records import Source
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
