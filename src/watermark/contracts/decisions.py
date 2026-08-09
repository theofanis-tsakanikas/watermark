"""The decision contract, and the two refusals that are structural rather than configured.

**Claim 7 is enforced here, at load time, before any runtime path exists.** A contract with
`effect: significant_on_person` and `actuation: automatic` does not load. Not "is rejected by a
policy check", not "raises at actuation" — the object cannot be constructed, so there is no
program in which that combination runs. `docs/REGULATORY.md` explains why that matters: GDPR
Art. 22(1) is not engaged rather than being satisfied by a safeguard, and "we'll automate it
later" then requires changing a contract, in a diff, with a reviewer.

**A decision path with no fallback does not load either.** ADR-0001: on a grid, silence is not
the safe state. Every path declares the conservative deterministic action it takes when the
window has not closed or the features are stale, and a path that declared none would default to
doing nothing — which for curtailment means the substation keeps heating while nobody decides.

The two refusals are siblings. One stops a machine acting where a human must; the other stops a
machine failing to act where nobody will.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from watermark.core.time import Duration

#: What a decision does to the world if it is wrong.
Effect = Literal[
    # A physical consequence — a substation, a transformer, a charger. Nobody's rights are
    # engaged directly; the hazard is thermal.
    "physical",
    # A significant effect on a natural person: an inspection, a back-bill, a contract action.
    # GDPR Art. 22(1).
    "significant_on_person",
    # A published number. Wrong is expensive and correctable, and correction is a restatement.
    "commercial",
]

Actuation = Literal[
    # The system acts. There is no human in the loop and pretending otherwise would be
    # dishonest engineering — a five-second curtailment loop cannot wait for one.
    "automatic",
    # The system produces a position in a queue. A named human accepts or rejects, and the
    # actuation path does not exist without that record.
    "human_gated",
    # The system states a number and nothing moves.
    "advisory",
]


class FallbackRule(BaseModel):
    """The conservative deterministic action, and the four properties that make it one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str

    #: Whether the rule can be computed with no model. Declared and checked rather than
    #: assumed: a fallback that calls a model is a second primary path, and it fails in the
    #: same conditions as the first.
    uses_model: bool = False

    #: Whether it needs a feature from the online store. A fallback that reads the feature
    #: store is not a fallback from a feature-store outage.
    uses_features: bool = False

    #: The actions it may take, as a subset of what the primary path may take. A fallback that
    #: disconnects a charger where the model would have throttled it has escalated the system's
    #: authority at the exact moment the system knows least.
    permitted_actions: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _a_fallback_stands_alone(self) -> FallbackRule:
        if self.uses_model:
            raise ValueError(
                f"fallback '{self.id}' uses a model. That is a second primary path, and it is "
                "unavailable in exactly the conditions the first one is (ADR-0001)."
            )
        if self.uses_features:
            raise ValueError(
                f"fallback '{self.id}' reads the feature store. A fallback from a feature-store "
                "outage cannot need the feature store."
            )
        return self


class DecisionContract(BaseModel):
    """One decision path: what it consumes, what it may do, and what it does when it cannot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    owner: str

    #: The horizon. Not decoration: it is what a caller compares `WatermarkView.lag` against,
    #: and it is why the watermark module reports a lag instead of judging one — three
    #: decisions with three horizons cannot share a threshold.
    horizon_seconds: int = Field(gt=0)

    effect: Effect
    actuation: Actuation

    #: Feature ids this path reads. Cross-checked against the feature contracts by the loader.
    features: tuple[str, ...] = ()
    model: str | None = None

    fallback: FallbackRule
    #: Every action the primary path may take. The fallback's must be a subset.
    permitted_actions: tuple[str, ...] = Field(min_length=1)

    #: The article this path's obligations come from, so that a reader can check the claim
    #: rather than take it. See docs/REGULATORY.md, verified 2026-08-09.
    legal_basis: str
    hazard: str

    @property
    def horizon(self) -> Duration:
        return Duration.of_seconds(self.horizon_seconds)

    @model_validator(mode="after")
    def _nothing_significant_is_automatic(self) -> DecisionContract:
        """Claim 7, at load time.

        The automated path is structurally incapable of a consequential decision about a
        person, because the contract describing one cannot be constructed. GDPR Art. 22(1) is
        therefore not engaged, rather than being engaged and satisfied by a safeguard.
        """
        if self.effect == "significant_on_person" and self.actuation == "automatic":
            raise ValueError(
                f"decision '{self.id}' has a significant effect on a person and declares "
                "automatic actuation. GDPR Art. 22(1) gives a data subject the right not to be "
                "subject to a decision based solely on automated processing that significantly "
                "affects them. This contract does not load — the combination has no runtime "
                "representation, which is what claim 7 means by structurally incapable."
            )
        return self

    @model_validator(mode="after")
    def _the_fallback_may_not_exceed_the_primary_path(self) -> DecisionContract:
        excess = sorted(set(self.fallback.permitted_actions) - set(self.permitted_actions))
        if excess:
            raise ValueError(
                f"decision '{self.id}': its fallback may take {excess}, which the primary path "
                "may not. A fallback that escalates the system's authority does so at the "
                "moment the system knows least."
            )
        return self

    @model_validator(mode="after")
    def _a_model_path_names_its_model(self) -> DecisionContract:
        if self.features and self.model is None and self.actuation == "automatic":
            raise ValueError(
                f"decision '{self.id}' reads features, actuates automatically, and names no "
                "model. Either it is a deterministic rule — in which case it needs no features "
                "beyond the ones the fallback uses — or the model is missing from the contract "
                "and nothing can record which version took the decision (AI Act Art. 12)."
            )
        return self
