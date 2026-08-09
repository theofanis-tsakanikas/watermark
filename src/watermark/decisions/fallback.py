"""The deterministic fallback rules. No model, no feature store, no clock.

Each one is chosen by name from a decision contract's `fallback.id`, so adding a rule is adding
a function and naming it — and a contract naming one that does not exist raises rather than
degrading into silence. That distinction matters more than it looks: on this system silence is
the safe state for exactly one of three paths, so "no rule found, do nothing" would be correct
for settlement, harmless for the anomaly queue, and would let a substation overload.
"""

from __future__ import annotations

from collections.abc import Callable

from watermark.core.records import SubstationTelemetry

#: How hard the fallback throttles per basis point over the limit. Not a tuning knob: it is
#: chosen so that a substation at 110% of its limit is cut to roughly half, which is more than
#: a forecast would ever ask for. ADR-0001 — the fallback is deliberately more aggressive than
#: the model, because it costs charging speed and does not cost the substation.
_THROTTLE_PER_BASIS_POINT = 5


def proportional_throttle(entity_id: str, telemetry: SubstationTelemetry | None) -> str | None:
    """Curtailment's fallback: throttle in proportion to measured overload.

    **It reads the raw telemetry the stream already carries, not the feature store.** That is
    not a detail. The contract declares `uses_features: false`, and an earlier draft of this
    function read `substation_headroom_15m` out of the served features — which claim 4's
    harness caught. A fallback that reads the feature store is not a fallback from a
    feature-store outage: it is unavailable in exactly the conditions the primary path is,
    which is the one property ADR-0001 requires it not to have.

    The returned action carries its own severity, so the actuator needs to consult nothing to
    know how hard to throttle. A bare `throttle` with the amount looked up elsewhere would need
    the thing that is broken.
    """
    if telemetry is None:
        # No measurement at all. The conservative action is not a bigger throttle — throttling
        # on no information is acting on nothing — it is holding whatever limit is already in
        # force, which is the last thing anybody decided with data in front of them.
        return "throttle:hold"
    if telemetry.headroom_w >= 0:
        return None

    # How far over the limit, in basis points of the limit itself.
    over = (-telemetry.headroom_w * 10_000) // max(1, telemetry.limit_w)
    reduction = min(90, over * _THROTTLE_PER_BASIS_POINT // 100)
    return f"throttle:{reduction}"


def no_queue_entry(entity_id: str, telemetry: SubstationTelemetry | None) -> str | None:
    """The anomaly path's fallback: produce nothing.

    The one path where silence genuinely is the safe state, and ADR-0001 explains why the
    distinction is not arbitrary — no inspector is dispatched and nothing in the physical world
    moves. Returning `None` is what makes the engine record `WITHHELD` rather than `FALLBACK`,
    which is a different fact and is counted separately.
    """
    return None


def withhold_and_restate(entity_id: str, telemetry: SubstationTelemetry | None) -> str | None:
    """Settlement's fallback: publish nothing now, restate when the data arrives.

    A number not yet stated has no consequence; a wrong one invoiced does. Doctrine 4 handles
    what happens next — the correction never erases what was previously stated.
    """
    return None


#: The registry. A contract's `fallback.id` indexes into this, and `DecisionEngine` raises on a
#: miss rather than treating an unknown rule as "do nothing".
RULES: dict[str, Callable[[str, SubstationTelemetry | None], str | None]] = {
    "proportional_on_measured_load": proportional_throttle,
    "no_queue_entry": no_queue_entry,
    "withhold_and_restate": withhold_and_restate,
}
