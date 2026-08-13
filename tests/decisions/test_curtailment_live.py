"""Curtailment against measured load — the decision with a physical consequence.

`CLAUDE.md` puts curtailment first and argues it high-risk under AI Act Annex III(2). The core
has carried `SubstationTelemetry` since phase 1 and the contract has declared a fallback computed
from it. Nothing produced one until `data/telemetry.py`, so the engine ran with `telemetry=None`,
withheld every time, and the decision was never taken. These tests are what stops that being true
again offline; `capture.yml` asserts the same properties against a live estate.
"""

from __future__ import annotations

import pytest

from data import cast
from data.telemetry import OVERLOADED_SUBSTATION, readings
from watermark.contracts import load
from watermark.core.records import SubstationTelemetry
from watermark.core.time import Duration, Instant
from watermark.core.watermarks import WatermarkStatus, WatermarkView
from watermark.decisions.engine import DecisionEngine, Origin

CONTRACTS = load()
CURTAILMENT = CONTRACTS.decisions["curtailment"]


def _view() -> WatermarkView:
    return WatermarkView(
        status=WatermarkStatus.ADVANCING,
        watermark=cast.DAY_START,
        idle=(),
        holding_back=None,
        lag=Duration.of_millis(0),
        leader=None,
    )


def _decide(telemetry: SubstationTelemetry | None):
    return DecisionEngine(CURTAILMENT, CONTRACTS.features).decide(
        entity_id="SUB-01",
        at=cast.DAY_START,
        served={},
        view=_view(),
        model_action=None,
        telemetry=telemetry,
    )


def _telemetry(load_w: int, limit_w: int) -> SubstationTelemetry:
    at = Instant.from_iso("2026-03-14T19:00:00Z")
    return SubstationTelemetry("SUB-01", at, at, load_w, limit_w)


def test_the_generator_drives_exactly_one_substation_over_its_limit() -> None:
    """Both answers have to be reachable, and only one substation may be the one over.

    A profile that never crosses a limit makes every decision `release` and a broken comparison
    indistinguishable from a working one. A profile where everything is over does the same thing
    in the other direction — "over the limit" as the normal state tests the threshold exactly as
    poorly as never reaching it. The first version of this generator did the second, at 140% on
    every substation.
    """
    over = {r.substation_id for r in readings() if r.load_w > r.limit_w}
    assert over == {OVERLOADED_SUBSTATION}


def test_the_limit_on_a_reading_is_the_one_in_force_at_that_instant() -> None:
    """Point-in-time, not looked up once. The limit moves at noon.

    A reading carrying the wrong limit makes every decision taken against it wrong in a way the
    decision record cannot show, because the record states the limit it was judged against.
    """
    limits = cast.substation_limits()
    for sample in readings():
        declared = limits.attribute(sample.substation_id, sample.event_time, "limit_w")
        assert sample.limit_w == int(str(declared))


def test_an_overloaded_substation_is_throttled_in_proportion() -> None:
    decision = _decide(_telemetry(load_w=480_000, limit_w=450_000))
    assert decision.origin is Origin.FALLBACK
    assert decision.action.startswith("throttle:")
    assert decision.action != "throttle:hold"


def test_a_substation_with_headroom_is_not_throttled() -> None:
    decision = _decide(_telemetry(load_w=300_000, limit_w=450_000))
    assert decision.action == "none"
    assert decision.origin is Origin.WITHHELD


def test_no_measurement_at_all_holds_rather_than_throttling_harder() -> None:
    """Throttling on no information is acting on nothing.

    The conservative action with no data is to hold whatever limit is already in force — the
    last thing anybody decided with data in front of them.
    """
    decision = _decide(None)
    assert decision.action == "throttle:hold"
    assert decision.origin is Origin.FALLBACK


def test_the_fallback_marker_reaches_the_record() -> None:
    """Doctrine 2. A fallback that looks like a model decision is worse than an outage."""
    row = _decide(_telemetry(load_w=480_000, limit_w=450_000)).as_row()
    assert row["origin"] == Origin.FALLBACK.value
    assert row["unavailable"]
    assert row["model_version"] is None


def test_the_worse_the_overload_the_harder_the_throttle() -> None:
    mild = _decide(_telemetry(load_w=460_000, limit_w=450_000)).action
    severe = _decide(_telemetry(load_w=600_000, limit_w=450_000)).action
    assert int(mild.split(":")[1]) < int(severe.split(":")[1])


@pytest.mark.parametrize("limit_w", [0, -1])
def test_a_substation_with_no_capacity_does_not_divide_by_zero(limit_w: int) -> None:
    """A division error inside a safety path is the worst possible place for one."""
    assert _decide(_telemetry(load_w=1, limit_w=limit_w)).action.startswith("throttle:")
