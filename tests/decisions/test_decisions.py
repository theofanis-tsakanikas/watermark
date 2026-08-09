"""The decision contract's two structural refusals, and the engine's ordering."""

from __future__ import annotations

import pytest

from watermark.contracts import load
from watermark.contracts.decisions import DecisionContract, FallbackRule
from watermark.core.records import SubstationTelemetry
from watermark.core.time import Instant
from watermark.decisions.fallback import proportional_throttle
from watermark.decisions.oversight import Actuation, OversightQueue, Review, Verdict

CONTRACTS = load()


def at(minute: int) -> Instant:
    return Instant.from_iso(f"2026-03-14T09:{minute:02d}:00Z")


class TestClaimSevenAtLoadTime:
    def test_a_significant_automatic_decision_does_not_load(self) -> None:
        definition = CONTRACTS.decisions["meter_anomaly"].model_dump()
        definition["actuation"] = "automatic"
        with pytest.raises(ValueError, match=r"Art\. 22"):
            DecisionContract.model_validate(definition)

    def test_a_significant_human_gated_decision_loads(self) -> None:
        """The calibration. Refusing the whole effect would make the anomaly path impossible
        rather than gated, and claim 7 would be satisfied by having no product."""
        assert CONTRACTS.decisions["meter_anomaly"].actuation == "human_gated"

    def test_a_physical_automatic_decision_loads(self) -> None:
        """Curtailment actuates automatically and must. There is no human in a five-second
        loop and pretending otherwise would be dishonest engineering."""
        assert CONTRACTS.decisions["curtailment"].actuation == "automatic"


class TestTheFallbackRule:
    def test_a_fallback_that_uses_a_model_is_refused(self) -> None:
        with pytest.raises(ValueError, match="second primary path"):
            FallbackRule(id="x", description="d", uses_model=True, permitted_actions=("throttle",))

    def test_a_fallback_that_reads_the_feature_store_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot need the feature store"):
            FallbackRule(
                id="x", description="d", uses_features=True, permitted_actions=("throttle",)
            )

    def test_a_fallback_may_not_exceed_the_primary_path(self) -> None:
        """A fallback that disconnects where the model would have throttled has escalated the
        system's authority at the moment the system knows least."""
        definition = CONTRACTS.decisions["curtailment"].model_dump()
        definition["fallback"]["permitted_actions"] = ["disconnect"]
        with pytest.raises(ValueError, match="escalates"):
            DecisionContract.model_validate(definition)


class TestTheCurtailmentFallback:
    def test_no_measurement_holds_the_limit_in_force(self) -> None:
        """Throttling on no information is acting on nothing. The conservative action is the
        last thing anybody decided with data in front of them."""
        assert proportional_throttle("SUB-01", None) == "throttle:hold"

    def test_a_substation_within_its_limit_is_left_alone(self) -> None:
        telemetry = SubstationTelemetry("SUB-01", at(10), at(10), 300_000, 400_000)
        assert proportional_throttle("SUB-01", telemetry) is None

    def test_it_throttles_in_proportion_to_the_overload(self) -> None:
        light = SubstationTelemetry("SUB-01", at(10), at(10), 420_000, 400_000)
        heavy = SubstationTelemetry("SUB-01", at(10), at(10), 480_000, 400_000)
        assert proportional_throttle("SUB-01", light) != proportional_throttle("SUB-01", heavy)

    def test_it_never_asks_for_more_than_ninety_per_cent(self) -> None:
        """Bounded. A fallback with no ceiling is a fallback that can disconnect a hospital by
        arithmetic."""
        extreme = SubstationTelemetry("SUB-01", at(10), at(10), 4_000_000, 400_000)
        assert proportional_throttle("SUB-01", extreme) == "throttle:90"


class TestTheOversightQueue:
    def test_an_actuation_needs_a_review_object(self) -> None:
        with pytest.raises(TypeError):
            Actuation(entry=None)  # type: ignore[call-arg]

    def test_a_review_of_an_entry_that_does_not_exist_is_refused(self) -> None:
        """A signature on a decision that was never presented."""
        with pytest.raises(KeyError, match="review of nothing"):
            OversightQueue().record(Review("ghost", "a.inspector", Verdict.ACCEPTED, at(10)))

    def test_rejections_are_exposed_as_a_training_signal(self) -> None:
        """The feedback loop SCENARIO.md names as the source of the proxy-discrimination risk.
        Exposing it deliberately is what lets the bias analysis measure the loop rather than
        inherit it."""
        from watermark.decisions.engine import Decision, Origin  # noqa: PLC0415

        queue = OversightQueue()
        decision = Decision(
            "meter_anomaly",
            "M1",
            at(10),
            "queue_for_inspection",
            Origin.MODEL,
            None,
            {},
            {},
            "v1",
            "advancing",
        )
        queue.enqueue("e1", decision)
        queue.record(Review("e1", "a.inspector", Verdict.REJECTED, at(11), reason="meter replaced"))
        assert [review.reason for _, review in queue.training_signal()] == ["meter replaced"]
