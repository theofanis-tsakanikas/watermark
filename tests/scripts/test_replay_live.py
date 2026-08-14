"""Claim 2's live comparison, driven offline over synthetic deliveries.

`scripts/replay_live.py` compares two deliveries of the same generated day against a live estate.
It cannot be run from a laptop — but the *comparison* is a pure function of two sets of landing
lines, and that is where all four of its earlier mistakes lived. Each of them keyed the two runs
on a landmark that moves when the runs differ slightly, and each produced thousands of spurious
disagreements on a system where nothing was wrong.

So the alignment is exercised here, at a scale where a real defect and an artefact look
different: forty meters, ninety-six windows, an arbitrary shift between the runs, and extra
windows at both ends.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GRID = 15 * 60 * 1000


@pytest.fixture(scope="module")
def replay():
    spec = importlib.util.spec_from_file_location(
        "replay_live", ROOT / "scripts" / "replay_live.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["replay_live"] = module
    spec.loader.exec_module(module)
    return module


def _day(base: int, *, extra_head: int = 0, extra_tail: int = 0, corrupt=None) -> list[dict]:
    """One generated day, at an arbitrary offset, optionally with edge differences."""
    rows = []
    for meter_index in range(40):
        meter = f"M{meter_index:05d}"
        start = -extra_head if meter_index == 0 else 0
        end = 96 + (extra_tail if meter_index == 1 else 0)
        for window in range(start, end):
            energy = 100 + meter_index + window
            if corrupt == (meter, window):
                energy += 7
            rows.append(
                {
                    "kind": "published",
                    "meter": meter,
                    "interval_start": base + window * GRID,
                    "revision": 0,
                    "energy_wh": str(energy),
                }
            )
    return rows


def test_the_same_day_at_a_different_offset_is_identical(replay) -> None:
    """The property claim 2 states, and the one every earlier key got wrong.

    `data/publish.py` shifts the day to end at the moment of the run, so two deliveries never
    share an instant. An offset of nine seconds or nine hours must make no difference.
    """
    first = replay.published_values(_day(1_000_000))
    second, offset = replay.align(first, replay.published_values(_day(9_998_123_456)))
    assert offset != 0

    # The overlap first, because that is where a misalignment shows. `compare` looks only at the
    # windows both runs closed, so two runs that share no key at all disagree about nothing —
    # which is why `main` refuses on the overlap before it ever calls `compare`.
    assert len(set(first) & set(second)) == len(first) >= replay.MINIMUM_OVERLAP * len(first)
    assert not replay.compare(first, second)


def test_extra_windows_at_either_end_do_not_move_the_alignment(replay) -> None:
    """A compressed day loses or gains a window at the edges; that is timing, not behaviour.

    Keyed on the earliest window this failed outright, and keyed on a meter's own window rank it
    failed the moment anything was added at the *head* — `replay[1] == first[0]` all the way down.
    """
    first = replay.published_values(_day(1_000_000))
    replayed = replay.published_values(_day(9_998_123_456, extra_head=1, extra_tail=1))
    second, _ = replay.align(first, replayed)
    shared = set(first) & set(second)
    assert len(shared) / len(first) > 0.99
    assert not [key for key in shared if first[key] != second[key]]


def test_a_single_corrupted_watt_hour_is_found_and_named(replay) -> None:
    """The reason the comparison exists. It must survive every allowance above it."""
    first = replay.published_values(_day(1_000_000))
    replayed = replay.published_values(
        _day(9_998_123_456, extra_head=1, extra_tail=1, corrupt=("M00013", 50))
    )
    second, _ = replay.align(first, replayed)
    problems = replay.compare(first, second)
    assert problems
    assert any("M00013" in problem for problem in problems)


def test_delivery_counts_are_not_compared(replay) -> None:
    """At-least-once delivery moves them, and claim 2 is not about them.

    A second delivery into a running stream lands in windows the first already closed and is
    absorbed as corrections. The live harness once reported 3,767 disagreements on a run where
    every energy figure matched, because it hashed the counts too — which `evals/replay/` had
    already written down as the wrong thing to do.
    """
    assert replay.SETTLED_FIELDS == ("energy_wh",)


def test_an_empty_delivery_is_not_a_pass(replay, tmp_path, capsys) -> None:
    """Two empty sets agree perfectly."""
    before = tmp_path / "before.txt"
    before.write_text("", encoding="utf-8")
    first = tmp_path / "first"
    after = tmp_path / "after"
    first.mkdir()
    after.mkdir()
    (first / "a.jsonl").write_text(
        "\n".join(json.dumps(row) for row in _day(1_000_000)), encoding="utf-8"
    )
    # `after` holds only what `first` did, so the replay produced nothing at all.
    (after / "a.jsonl").write_text(
        (first / "a.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )

    code = replay.main(["--before", str(before), "--first", str(first), "--after", str(after)])
    assert code == 1
    assert "published nothing" in capsys.readouterr().out
