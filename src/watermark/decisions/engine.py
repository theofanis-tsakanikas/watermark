"""Producing a decision, or falling back — and never confusing the two."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from watermark.contracts.decisions import DecisionContract
from watermark.contracts.features import FeatureContract
from watermark.core.records import SubstationTelemetry
from watermark.core.time import Instant
from watermark.core.watermarks import WatermarkView
from watermark.features.online import ServedValue


class Origin(Enum):
    """Where a decision came from. Carried to the end of everything (doctrine 2)."""

    MODEL = "model"
    #: A deterministic rule the contract declares, taken because the primary path could not be.
    FALLBACK = "fallback"
    #: No decision at all — legitimate only where the contract says silence is safe.
    WITHHELD = "withheld"


class Unavailable(Enum):
    """Why the primary path could not be taken. A closed vocabulary, so it can be counted.

    "How often does this actually happen?" is the first question asked of a fallback rate, and
    a free-text reason makes it unanswerable.
    """

    WINDOW_NOT_CLOSED = "window_not_closed"
    WATERMARK_STALLED = "watermark_stalled"
    FEATURE_STALE = "feature_stale"
    FEATURE_MISSING = "feature_missing"
    MODEL_UNAVAILABLE = "model_unavailable"


def identity_of(contract_id: str, entity_id: str, at: Instant) -> str:
    """A decision's identifier: the contract, the subject and the instant, hashed.

    The same shape as `watermark.lineage.identity` mints for readings and results, and for the
    same reason: **derived, not random.** Claim 2 is that a replay produces identical bytes, and
    a uuid would make every rerun differ in the one field a decision record is looked up by.

    The contract id is in the material so that two decisions taken about the same meter at the
    same instant under different contracts — a curtailment and an anomaly flag — do not collide.
    """
    material = f"decision|{contract_id}|{entity_id}|{at.epoch_millis}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class Decision:
    """One decision, and everything AI Act Art. 12 asks a record to contain.

    Inputs *as served*, not as they are now: a record that re-reads the feature store at audit
    time describes a decision nobody took.
    """

    #: Unique to *this* decision: the contract, the entity and the instant it was taken at.
    #:
    #: **It used to be the contract id**, which meant every decision this platform has ever taken
    #: about every person carried the identifier `meter_anomaly`. It read as an identifier and
    #: was a category. The first live run caught it in the worst place available: the oversight
    #: queue is a mapping keyed on this, so twenty decisions about twenty different people
    #: collapsed into one entry and nineteen were dropped — silently, from the structure claim 7
    #: exists to guarantee. It refused to actuate the one that survived, correctly, and the
    #: other nineteen were never presented to anybody at all.
    #:
    #: Derived rather than random, because claim 2 requires a replay to produce identical bytes.
    #: A uuid here would make every rerun differ in the one field a record is looked up by.
    decision_id: str
    entity_id: str
    at: Instant
    action: str
    origin: Origin
    #: Set whenever the origin is not MODEL. `None` on a model decision, which is what makes
    #: "why did this fall back?" answerable without joining anything.
    unavailable: Unavailable | None
    #: The feature values the decision was taken on, exactly as served.
    inputs: Mapping[str, int]
    #: The staleness of each input at the moment of the decision. Recorded rather than
    #: recomputed: the budget it was judged against may change, and the judgement must not.
    input_ages: Mapping[str, int]
    model_version: str | None
    #: The watermark that permitted it, or the reason there was none. Claim 1's evidence,
    #: carried onto the decision as well as onto the window.
    watermark_status: str

    @property
    def is_fallback(self) -> bool:
        return self.origin is Origin.FALLBACK

    def as_row(self) -> dict[str, object]:
        """The record, flat and explicit.

        `origin` is a top-level column and not a flag inside a JSON blob, because the number
        anybody eventually wants is the fallback *rate*, and a rate over a JSON field is a rate
        nobody computes.
        """
        return {
            "decision_id": self.decision_id,
            "entity_id": self.entity_id,
            "at": self.at.to_iso(),
            "action": self.action,
            "origin": self.origin.value,
            "unavailable": None if self.unavailable is None else self.unavailable.value,
            "inputs": dict(sorted(self.inputs.items())),
            "input_ages_ms": dict(sorted(self.input_ages.items())),
            "model_version": self.model_version,
            "watermark_status": self.watermark_status,
        }


@dataclass(frozen=True, slots=True)
class DecisionEngine:
    """Applies one decision contract. Nothing here is decided by configuration at runtime."""

    contract: DecisionContract
    features: Mapping[str, FeatureContract]

    def decide(  # noqa: PLR0917 — see the note on `_decision`
        self,
        entity_id: str,
        at: Instant,
        served: Mapping[str, ServedValue | None],
        view: WatermarkView,
        model_action: str | None = None,
        model_version: str | None = None,
        telemetry: SubstationTelemetry | None = None,
    ) -> Decision:
        """Take the decision, or the fallback, and say which.

        The order is the argument. **Availability is judged before the model is consulted**, so
        a model that would have produced a plausible answer from stale inputs never gets the
        chance — claim 4 is not a check on the output, it is a gate in front of the input.

        `telemetry` is the last raw measurement, handed in separately from the served features.
        It is separate because the fallback may not read the feature store (ADR-0001), and the
        cleanest way to guarantee that is for the fallback never to be given it.
        """
        blocked = self._why_unavailable(served, at, view)
        if blocked is None and model_action is not None:
            return self._decision(
                entity_id, at, model_action, Origin.MODEL, None, served, model_version, view
            )

        reason = blocked or Unavailable.MODEL_UNAVAILABLE
        action = self._fallback_action(entity_id, telemetry)
        origin = Origin.WITHHELD if action is None else Origin.FALLBACK
        return self._decision(entity_id, at, action or "none", origin, reason, served, None, view)

    def _why_unavailable(
        self,
        served: Mapping[str, ServedValue | None],
        at: Instant,
        view: WatermarkView,
    ) -> Unavailable | None:
        """The first reason the primary path cannot be taken, or None.

        The watermark is checked before the features. A stalled stream makes every feature
        stale as a consequence, and reporting the consequence instead of the cause sends
        somebody to look at the feature store while the stream is the thing that is stuck.
        """
        if not view.status.will_resolve_itself:
            return Unavailable.WATERMARK_STALLED
        if not view.status.may_close_windows:
            return Unavailable.WINDOW_NOT_CLOSED

        for feature_id in self.contract.features:
            value = served.get(feature_id)
            if value is None:
                return Unavailable.FEATURE_MISSING
            budget = self.features[feature_id].freshness_budget
            if not value.is_fresh_at(at, budget):
                return Unavailable.FEATURE_STALE
        return None

    def _fallback_action(self, entity_id: str, telemetry: SubstationTelemetry | None) -> str | None:
        """What the declared fallback does. `None` where the contract says silence is safe.

        The rule itself lives in `fallback.py` and is chosen by the contract's `fallback.id` —
        so adding a rule is adding a function and naming it, and a contract naming one that
        does not exist fails to load rather than falling back to nothing at runtime.
        """
        from watermark.decisions.fallback import RULES  # noqa: PLC0415 — avoids a cycle

        rule = RULES.get(self.contract.fallback.id)
        if rule is None:
            raise KeyError(
                f"decision '{self.contract.id}' declares fallback "
                f"'{self.contract.fallback.id}', which has no implementation. A missing "
                "fallback must not degrade into silence: on this system silence is the safe "
                "state for exactly one of three paths."
            )
        return rule(entity_id, telemetry)

    def _decision(  # noqa: PLR0917
        self,
        entity_id: str,
        at: Instant,
        action: str,
        origin: Origin,
        unavailable: Unavailable | None,
        served: Mapping[str, ServedValue | None],
        model_version: str | None,
        view: WatermarkView,
    ) -> Decision:
        inputs = {
            feature_id: value.value for feature_id, value in served.items() if value is not None
        }
        ages = {
            feature_id: value.age_at(at).millis
            for feature_id, value in served.items()
            if value is not None
        }
        return Decision(
            decision_id=identity_of(self.contract.id, entity_id, at),
            entity_id=entity_id,
            at=at,
            action=action,
            origin=origin,
            unavailable=unavailable,
            inputs=inputs,
            input_ages=ages,
            model_version=model_version,
            watermark_status=view.status.value,
        )


def fallback_rate(decisions: list[Decision]) -> float:
    """The proportion of decisions that came from a fallback.

    A first-class metric, not an afterthought. A path that has silently been running on
    fallback for a week is an outage nothing has reported — every individual decision was
    correct and conservative, and the aggregate is the only place the outage is visible.
    """
    if not decisions:
        return 0.0
    return sum(1 for decision in decisions if decision.is_fallback) / len(decisions)
