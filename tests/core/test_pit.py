"""Point-in-time resolution, and the three defects that resolve silently to a wrong answer."""

from __future__ import annotations

from watermark.core.pit import History, Version, problems
from watermark.core.time import Instant


def at(day: int, hour: int = 0) -> Instant:
    return Instant.from_iso(f"2026-03-{day:02d}T{hour:02d}:00:00Z")


def version(entity: str, start: Instant, end: Instant | None, **attributes: str) -> Version:
    return Version(entity, start, end, attributes)


TARIFF_HISTORY = History.of(
    "tariff",
    [
        version("M1", at(1), at(15), tariff="T-WINTER"),
        version("M1", at(15), None, tariff="T-SPRING"),
    ],
)


class TestResolution:
    def test_a_reading_resolves_against_the_tariff_in_force_at_its_own_time(self) -> None:
        assert TARIFF_HISTORY.attribute("M1", at(10), "tariff") == "T-WINTER"
        assert TARIFF_HISTORY.attribute("M1", at(20), "tariff") == "T-SPRING"

    def test_the_boundary_belongs_to_the_new_version(self) -> None:
        """Half-open, `[valid_from, valid_to)`. Which side wins matters less than the choice
        being made once: closed on both sides makes two versions valid at the boundary, and
        which one a query returns becomes a property of its ORDER BY."""
        assert TARIFF_HISTORY.attribute("M1", at(15), "tariff") == "T-SPRING"

    def test_before_the_first_version_there_is_no_version(self) -> None:
        """`None` is a real answer. A meter has no customer before it is installed, and
        returning the nearest version instead invents a commercial relationship that did not
        exist — most often at exactly the boundary a dispute is about."""
        assert TARIFF_HISTORY.resolve("M1", Instant.from_iso("2026-02-01T00:00:00Z")) is None

    def test_an_unknown_entity_resolves_to_nothing(self) -> None:
        assert TARIFF_HISTORY.resolve("M-UNKNOWN", at(10)) is None

    def test_a_gap_is_not_filled(self) -> None:
        """A meter uninstalled for a month has a real gap, and the readings in it genuinely
        have nobody to bill."""
        history = History.of(
            "assignment",
            [
                version("M1", at(1), at(10), customer="C1"),
                version("M1", at(20), None, customer="C2"),
            ],
        )
        assert history.resolve("M1", at(15)) is None

    def test_resolution_does_not_depend_on_input_order(self) -> None:
        """SCD-2 rows arrive from a CDC pipeline in whatever order the log emitted them."""
        forwards = History.of(
            "t", [version("M1", at(1), at(15), v="a"), version("M1", at(15), None, v="b")]
        )
        backwards = History.of(
            "t", [version("M1", at(15), None, v="b"), version("M1", at(1), at(15), v="a")]
        )
        assert forwards == backwards
        assert backwards.attribute("M1", at(10), "v") == "a"


class TestProblems:
    def test_a_clean_history_has_none(self) -> None:
        assert problems(TARIFF_HISTORY) == ()

    def test_overlapping_versions_are_reported(self) -> None:
        """Two tariffs in force at once. Resolution returns whichever sorts first — stable,
        arbitrary, and worse than an error, because nothing ever looks wrong."""
        overlapping = History.of(
            "tariff",
            [
                version("M1", at(1), at(20), tariff="T-WINTER"),
                version("M1", at(15), None, tariff="T-SPRING"),
            ],
        )
        found = problems(overlapping)
        assert [problem.kind for problem in found] == ["overlapping_versions"]

    def test_a_version_left_open_before_another_begins_is_an_overlap(self) -> None:
        never_closed = History.of(
            "tariff",
            [version("M1", at(1), None, tariff="A"), version("M1", at(15), at(20), tariff="B")],
        )
        assert any(p.kind == "overlapping_versions" for p in problems(never_closed))

    def test_a_backwards_interval_is_reported(self) -> None:
        """It covers nothing, so every reading in the period resolves to no version and quietly
        loses its customer."""
        backwards = History.of("tariff", [version("M1", at(20), at(10), tariff="T")])
        assert [p.kind for p in problems(backwards)] == ["backwards_interval"]

    def test_two_open_versions_are_reported(self) -> None:
        """The entity has two presents, and every future reading resolves to one of them by
        sort order."""
        two_presents = History.of(
            "tariff",
            [version("M1", at(1), None, tariff="A"), version("M1", at(15), None, tariff="B")],
        )
        kinds = {p.kind for p in problems(two_presents)}
        assert "multiple_open_versions" in kinds

    def test_problems_are_reported_in_a_stable_order(self) -> None:
        messy = History.of(
            "tariff",
            [
                version("M2", at(20), at(10), tariff="X"),
                version("M1", at(1), at(20), tariff="A"),
                version("M1", at(15), None, tariff="B"),
            ],
        )
        assert [p.entity_id for p in problems(messy)] == ["M1", "M2"]
