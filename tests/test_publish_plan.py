"""The capture publisher's plan, which is the part that can be checked without an estate."""

from __future__ import annotations

from data.publish import plan


def test_the_plan_covers_the_whole_generated_day() -> None:
    assert plan(30).deliveries > 4000


def test_a_shorter_window_compresses_harder() -> None:
    """A day in thirty minutes is 48x real time, and the burst compresses with it. A capture
    that published evenly would exercise the average and prove nothing about the peak the
    shard count was sized against."""
    assert plan(30).compression > plan(60).compression


def test_the_peak_is_reported_not_the_average() -> None:
    """The number that decides whether the stream throttles is records per second at the front
    of a burst, not the day's mean."""
    assert plan(30).records_per_second_peak > 0


def test_the_description_names_the_compression() -> None:
    assert "x into the capture window" in plan(30).describe()
