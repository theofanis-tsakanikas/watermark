"""Event time has exactly one definition, and these are the properties that make it one."""

from __future__ import annotations

import pytest

from watermark.core.time import Duration, EventTimeError, Instant


class TestParsing:
    def test_an_offset_is_required(self) -> None:
        """A device timestamp is never assumed to be UTC.

        This is the whole reason the parser exists. A meter reporting local time, parsed as
        if it were UTC, lands in the wrong window; every window still closes, every total
        still balances against itself, and nothing anywhere reports an error.
        """
        with pytest.raises(EventTimeError, match="no UTC offset"):
            Instant.from_iso("2026-03-14T09:15:00")

    @pytest.mark.parametrize(
        "text",
        [
            "2026-03-14T09:15:00Z",
            "2026-03-14T09:15:00+00:00",
            "2026-03-14T11:15:00+02:00",
            "2026-03-14T04:15:00-05:00",
        ],
    )
    def test_one_instant_reached_from_four_offsets(self, text: str) -> None:
        assert Instant.from_iso(text) == Instant.from_iso("2026-03-14T09:15:00Z")

    def test_milliseconds_survive(self) -> None:
        assert Instant.from_iso("2026-03-14T09:15:00.250Z").epoch_millis % 1000 == 250

    def test_sub_millisecond_precision_is_floored_not_rounded(self) -> None:
        """Flooring can only move an event time earlier.

        Rounding can move it later, and an event time nudged later is exactly what makes a
        late reading look on time — the one direction of error this project exists to stop.
        `.9999` rounds to the next millisecond and floors to this one.
        """
        floored = Instant.from_iso("2026-03-14T09:15:00.999999Z")
        assert floored == Instant.from_iso("2026-03-14T09:15:00.999Z")

    def test_a_non_string_is_refused_rather_than_coerced(self) -> None:
        """An integer in a timestamp field is a schema variant to normalise, not a value to
        guess the unit of. Seconds and milliseconds differ by a factor of a thousand and both
        parse."""
        with pytest.raises(EventTimeError, match="not int"):
            Instant.from_iso(1_773_479_700)  # type: ignore[arg-type]

    def test_garbage_is_refused_with_the_text_in_the_message(self) -> None:
        with pytest.raises(EventTimeError, match="not ISO-8601"):
            Instant.from_iso("14/03/2026 09:15Z")


class TestRendering:
    def test_the_rendering_is_canonical(self) -> None:
        """Always UTC, always `Z`, always three decimals — including when they are zero.

        `datetime.isoformat()` drops the fractional part at exactly zero milliseconds, so two
        runs over the same data would agree on every value and differ in the bytes. Claim 2 is
        a claim about the bytes.
        """
        assert Instant.from_iso("2026-03-14T09:15:00Z").to_iso() == "2026-03-14T09:15:00.000Z"
        assert (
            Instant.from_iso("2026-03-14T11:15:00.070+02:00").to_iso() == "2026-03-14T09:15:00.070Z"
        )

    def test_parsing_a_rendering_returns_the_same_instant(self) -> None:
        original = Instant.from_iso("2026-03-14T09:15:00.007Z")
        assert Instant.from_iso(original.to_iso()) == original


class TestArithmetic:
    def test_a_difference_is_signed(self) -> None:
        """Lateness and clock skew are the same subtraction with opposite signs. Collapsing
        them into a magnitude throws away the only bit that tells them apart."""
        early = Instant.from_iso("2026-03-14T09:00:00Z")
        late = Instant.from_iso("2026-03-14T09:15:00Z")
        assert late.since(early) == Duration.of_minutes(15)
        assert early.since(late) == Duration.of_minutes(-15)

    def test_durations_are_exact_integers(self) -> None:
        assert Duration.of_minutes(15).millis == 900_000
        assert Duration.of_days(1) == Duration.of_hours(24)

    def test_a_float_duration_is_refused(self) -> None:
        """A 15-minute window built from a float is occasionally 899,999.9 ms long, and the
        reading on the boundary lands in whichever window the rounding happened to pick."""
        with pytest.raises(EventTimeError, match="integer count of milliseconds"):
            Duration(900_000.0)  # type: ignore[arg-type]

    def test_a_bool_is_not_an_integer_here(self) -> None:
        """`True` is an `int` in Python. A duration of `True` is a millisecond, silently."""
        with pytest.raises(EventTimeError):
            Duration(True)  # type: ignore[arg-type]

    def test_instants_order_and_hash(self) -> None:
        """All three uses at once: a sort key for window assignment, a set member for
        deduplication, a dictionary key for point-in-time resolution."""
        first = Instant.from_iso("2026-03-14T09:00:00Z")
        second = Instant.from_iso("2026-03-14T09:15:00Z")
        assert first < second
        assert sorted([second, first]) == [first, second]
        assert len({first, second, Instant.from_iso("2026-03-14T09:00:00.000Z")}) == 2


class TestAlignment:
    def test_alignment_is_to_the_epoch_so_everybody_agrees(self) -> None:
        """A 15-minute grain gives :00, :15, :30, :45 for anybody who asks, without a shared
        origin having to be agreed anywhere."""
        reading = Instant.from_iso("2026-03-14T09:22:37.412Z")
        assert reading.floor_to(Duration.of_minutes(15)).to_iso() == "2026-03-14T09:15:00.000Z"
        assert reading.floor_to(Duration.of_hours(1)).to_iso() == "2026-03-14T09:00:00.000Z"

    def test_an_instant_on_a_boundary_stays_where_it_is(self) -> None:
        boundary = Instant.from_iso("2026-03-14T09:15:00Z")
        assert boundary.floor_to(Duration.of_minutes(15)) == boundary

    def test_aligning_to_nothing_is_refused(self) -> None:
        with pytest.raises(EventTimeError, match="non-positive"):
            Instant.from_iso("2026-03-14T09:15:00Z").floor_to(Duration.of_minutes(0))
