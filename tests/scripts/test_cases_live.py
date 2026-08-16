"""The live case matrix, driven by the real emitter over one clean run.

`scripts/cases_live.py` reads the landing evidence a capture produces and asserts that each
defect the cast declares is visible in it. It cannot be exercised against an estate from a
laptop — but the *lines* it reads are produced by `streaming/operators._line`, which needs no
JVM, so the whole matrix can be driven offline over the same generated day.

That matters more than convenience. The first ad-hoc run of the matrix was against an accumulated
landing prefix — many captures, each shifted to a different day — and the evidence-gap case
failed, because a gap in one run is filled by another run's windows at different intervals. The
check was right and the data was not. These tests pin it to one run so the distinction cannot be
confused again.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data import cast  # noqa: E402
from data.generate import generate  # noqa: E402
from streaming.operators import _line  # noqa: E402
from watermark.core.time import Duration  # noqa: E402
from watermark.core.watermarks import WatermarkStatus, WatermarkView  # noqa: E402
from watermark.runner import Arrival, run  # noqa: E402


@pytest.fixture(scope="module")
def matrix():
    spec = importlib.util.spec_from_file_location("cases_live", ROOT / "scripts" / "cases_live.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it is executed: `@dataclass` resolves its annotations through
    # `sys.modules[cls.__module__]`, and a module loaded by path is not there yet — the class
    # body raises `AttributeError: 'NoneType' object has no attribute '__dict__'`, which reads
    # like a bug in the dataclass rather than in how it was imported.
    sys.modules["cases_live"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def evidence() -> list[dict]:
    """One run of the generated day, emitted through the real adapter line writer.

    `_line` is what the deployed operator writes, so the shapes these cases read are the shapes
    the estate produces — not a fixture agreeing with itself.
    """
    result = run(
        [Arrival(d.raw, d.ingest_time, d.source, d.partition) for d in generate()],
        cast.SUBSTATIONS,
    )
    view = WatermarkView(
        status=WatermarkStatus.ADVANCING,
        watermark=cast.DAY_END,
        idle=(),
        holding_back=None,
        lag=Duration.of_millis(0),
        leader=None,
    )
    lines = [
        json.loads(
            _line(
                kind,
                item,
                view,
                result.lineage.get(
                    (item.meter_id, item.interval_start.epoch_millis, item.revision)
                ),
            )
        )
        for kind, items in (
            ("published", result.published),
            ("restated", result.restated),
            ("confirmed", result.confirmed),
        )
        for item in items
    ]
    lines += [
        {"kind": "quarantine", "reason": q.reason.value, "payload": q.payload}
        for q in result.quarantined
    ]
    lines += [
        {
            "kind": "watermark",
            "status": tick.status.value,
            "holding_back": tick.holding_back,
        }
        for tick in result.ticks
    ]
    return lines


def test_the_evidence_gap_is_visible_in_one_clean_run(matrix, evidence) -> None:
    """The case that failed against an accumulated prefix, and the reason it did.

    A gap is a *shortfall against the fleet*, and a prefix holding several shifted days fills one
    run's hole with another run's windows. Over a single run the shortfall is real and the check
    finds it — which is why `capture.yml` passes the part files that existed beforehand.
    """
    assert matrix.the_evidence_gap_leaves_a_hole(evidence) == ""


def test_the_retrying_cohort_is_deduplicated(matrix, evidence) -> None:
    assert matrix.the_retrying_cohort_is_deduplicated(evidence) == ""


def test_a_wrong_clock_is_quarantined(matrix, evidence) -> None:
    assert matrix.a_wrong_clock_is_quarantined(evidence) == ""


def test_the_late_batch_names_what_it_replaced(matrix, evidence) -> None:
    assert matrix.the_late_batch_restates_and_names_what_it_replaced(evidence) == ""


def test_the_quiet_substation_names_a_culprit(matrix, evidence) -> None:
    assert matrix.the_quiet_substation_held_the_watermark_back(evidence) == ""


def test_more_than_one_condition_is_reached(matrix, evidence) -> None:
    assert matrix.every_condition_the_decisions_branch_on_is_reached(evidence) == ""


def test_an_empty_run_fails_every_case_rather_than_passing(matrix) -> None:
    """The guard that matters most: nothing must pass over no evidence.

    Every case here looks for something and reports its absence. Given an empty list they would
    each find nothing to contradict them, and a matrix that reports green over an outage is the
    exact failure this repository is written against.
    """
    for case in matrix.CASES:
        assert case.check([]) != "", f"{case.name} passed over no evidence at all"


def test_the_first_delivery_boundary_keeps_the_gap_visible(matrix, tmp_path) -> None:
    """`read_lines` with `only` is what restores the question, and it is checked on files
    rather than on rows — that is the form the workflow hands it."""
    landing = tmp_path / "landing"
    landing.mkdir()
    (landing / "first.jsonl").write_text('{"kind": "published", "meter": "M00001"}\n')
    (landing / "second.jsonl").write_text('{"kind": "published", "meter": "M00002"}\n')
    (landing / "history.jsonl").write_text('{"kind": "published", "meter": "M99999"}\n')

    everything = matrix.read_lines(landing, {"history.jsonl"})
    assert {row["meter"] for row in everything} == {"M00001", "M00002"}

    bounded = matrix.read_lines(landing, {"history.jsonl"}, {"first.jsonl"})
    assert {row["meter"] for row in bounded} == {"M00001"}


def test_a_capture_with_no_hold_back_is_reported_and_not_failed(matrix, capsys) -> None:
    """Pins a decision that was made twice and disagreed with itself once.

    `capture.yml` reports a missing `held_back` with its reasoning; this case used to fail the
    run for it. The two contradicted each other and nobody noticed until both were scoped to the
    same delivery. Eight seconds of wall clock decides whether the transition is observed, and a
    check that turns on that fails for a reason unrelated to claim 1.
    """
    healthy = [{"kind": "watermark", "status": "advancing", "holding_back": None}] * 3
    assert matrix.the_quiet_substation_held_the_watermark_back(healthy) == ""
    assert "eight seconds" in capsys.readouterr().out


def test_a_hold_back_that_names_nobody_still_fails(matrix) -> None:
    """The half that stays deterministic. A held-back watermark that cannot say who is holding
    it is the half that makes the state useless to an operator, and it is not a timing question."""
    anonymous = [
        {"kind": "watermark", "status": "advancing", "holding_back": None},
        {"kind": "watermark", "status": "held_back", "holding_back": None},
    ]
    assert matrix.the_quiet_substation_held_the_watermark_back(anonymous) != ""


def test_no_watermark_condition_at_all_still_fails(matrix) -> None:
    """Silence from the reporter is not the same as a healthy quiet grid — that distinction is
    the whole of claim 1, and it is what the emitter's heartbeat exists to make."""
    assert matrix.the_quiet_substation_held_the_watermark_back([]) != ""
