"""The live decider's pure halves, checked with no account.

`scripts/decide_live.py` is the first thing in this repository to run `src/watermark/decisions/`
against AWS. Two parts of it decide what the run *concludes*, and neither needs a cloud: how the
watermark view is reconstructed from the stream's evidence, and which properties are asserted
about the decisions that come out. Both are here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from watermark.core.time import Duration, Instant  # noqa: E402
from watermark.core.watermarks import WatermarkStatus, WatermarkView  # noqa: E402
from watermark.decisions.engine import Origin  # noqa: E402


@pytest.fixture(scope="module")
def decider():
    spec = importlib.util.spec_from_file_location(
        "decide_live", ROOT / "scripts" / "decide_live.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["decide_live"] = module
    spec.loader.exec_module(module)
    return module


def _view(status: WatermarkStatus) -> WatermarkView:
    return WatermarkView(
        status=status,
        watermark=Instant.from_iso("2026-03-14T10:00:00Z"),
        idle=(),
        holding_back=None,
        lag=Duration.of_millis(0),
        leader=None,
    )


def _row(origin: Origin, *, unavailable: str | None = None, age_ms: int = 0) -> dict:
    return {
        "decision_id": "d1",
        "origin": origin.value,
        "unavailable": unavailable,
        "input_ages_ms": {"meter_consumption_1h": age_ms},
    }


class _Feature:
    freshness_budget_seconds = 3600


def test_no_evidence_is_unstarted_and_not_a_healthy_silence(decider) -> None:
    """A stream that has never reported a condition is indistinguishable from one that never ran.

    Resolving that to a healthy view is how a decision gets taken against a job that is not
    running, and it is the exact shape of claim 1's failure.
    """
    view = decider.watermark_view([])
    assert view.status is WatermarkStatus.UNSTARTED
    assert not view.status.may_close_windows


def test_the_last_transition_wins(decider) -> None:
    """And it is read with the field names the operator actually emits.

    `at`, not `observed_at`; `idle_partitions`, not `idle`; epoch milliseconds, not ISO-8601.
    Every mismatch would read as a missing key, resolve to a default, and present a healthy view
    of a grid that was holding back — claim 1 failing in the reader rather than in the stream.
    """
    view = decider.watermark_view(
        [
            {
                "kind": "watermark",
                "status": "advancing",
                "at": 1_000,
                "watermark": 1_000,
                "lag_millis": 0,
            },
            {
                "kind": "watermark",
                "status": "held_back",
                "at": 2_000,
                "watermark": 1_500,
                "lag_millis": 1800000,
                "holding_back": "SUB-01",
                "idle_partitions": ["SUB-02"],
            },
            {"kind": "published", "meter_id": "M00001"},
        ]
    )
    assert view.status is WatermarkStatus.HELD_BACK
    assert view.holding_back == "SUB-01"
    assert view.idle == ("SUB-02",)
    assert view.lag.millis == 1800000


def test_a_model_decision_under_a_held_back_watermark_is_a_failure(decider) -> None:
    """Claim 1, on the decision path. The window check is not the whole of it.

    A watermark that has not advanced permits no close, so a decision that came out of the model
    anyway was taken on data the system does not claim to have seen.
    """
    problems = decider.properties_that_must_hold(
        [_row(Origin.MODEL)], _view(WatermarkStatus.HELD_BACK), _Feature()
    )
    assert any("claim 1" in problem for problem in problems)


def test_a_model_decision_under_an_advanced_watermark_is_fine(decider) -> None:
    assert not decider.properties_that_must_hold(
        [_row(Origin.MODEL)], _view(WatermarkStatus.ADVANCING), _Feature()
    )


def test_a_fallback_with_no_reason_recorded_is_a_failure(decider) -> None:
    """Doctrine 2. The marker is not the point — the marker surviving into the record is.

    A fallback that reaches the record looking like a model decision is worse than an outage,
    because it is silent and it trains somebody to trust it.
    """
    problems = decider.properties_that_must_hold(
        [_row(Origin.FALLBACK)], _view(WatermarkStatus.ADVANCING), _Feature()
    )
    assert any("no reason recorded" in problem for problem in problems)


def test_a_model_decision_carrying_a_reason_is_also_a_failure(decider) -> None:
    """The other direction, and it matters as much.

    `unavailable` on a model decision means something judged an input missing and the model ran
    regardless. The fallback rate is computed off `origin`, so this row would be counted as
    healthy for ever.
    """
    problems = decider.properties_that_must_hold(
        [_row(Origin.MODEL, unavailable="stale_feature")],
        _view(WatermarkStatus.ADVANCING),
        _Feature(),
    )
    assert any("carries a reason" in problem for problem in problems)


def test_a_model_decision_on_a_stale_feature_is_a_failure(decider) -> None:
    """Claim 4. The gate is in front of the input, so nothing past the budget reaches the model."""
    problems = decider.properties_that_must_hold(
        [_row(Origin.MODEL, age_ms=3_600_001)], _view(WatermarkStatus.ADVANCING), _Feature()
    )
    assert any("claim 4" in problem for problem in problems)


def test_a_fallback_on_a_stale_feature_is_the_correct_answer(decider) -> None:
    assert not decider.properties_that_must_hold(
        [_row(Origin.FALLBACK, unavailable="stale_feature", age_ms=3_600_001)],
        _view(WatermarkStatus.ADVANCING),
        _Feature(),
    )
