"""The entity contract schema. The model *is* the schema.

A separate JSON Schema beside this file would be a second description of one contract, and the
two diverge on the first busy afternoon. Everything a contract must satisfy is here, expressed
as validation that runs on every load.

Two rules refuse a contract outright rather than warning about it, and both are load-time
enforcement of something that would otherwise be a matter of remembering:

**Personal data must declare a purpose.** GDPR Art. 5(1)(b) requires personal data to be
collected for specified, explicit and legitimate purposes. A contract that says
`personal_data: true` and states no purpose does not load. This is the entity-level sibling of
the freshness-budget rule for features in phase 2, and it exists for the same reason: the thing
that must not be forgotten is made impossible to omit.

**A key may not be a tracked attribute.** An SCD-2 history keyed on something that changes is
not a history — it is two entities wearing one name, and every point-in-time resolution against
it silently returns whichever version sorts first.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Reference(BaseModel):
    """A foreign key into another entity, checked across the whole set by the loader."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: str
    via: str = Field(description="The attribute or key field holding the referenced id.")


class Scd2(BaseModel):
    """How an entity's history is recorded.

    Named fields rather than assumed ones: the CDC pipeline does not get to decide what a
    validity interval is called, and a point-in-time join that guessed would guess differently
    per table.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    valid_from: str
    valid_to: str
    tracked: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct_bounds(self) -> Scd2:
        if self.valid_from == self.valid_to:
            raise ValueError("valid_from and valid_to name the same field; an interval needs two")
        return self


class EntityContract(BaseModel):
    """One reference entity and the rules for resolving it as of an instant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    kind: Literal["reference"]
    owner: str
    source: str
    grain: str
    key: tuple[str, ...] = Field(min_length=1)

    #: Whether a natural person is identifiable from this entity, directly or by a join that
    #: exists in this platform. Declared rather than inferred, and deliberately wide: a
    #: pseudonymous identifier trivially linkable to a person is personal data, and the narrow
    #: reading is how a table ends up outside the erasure scope in claim 6.
    personal_data: bool
    purpose: str | None = None

    scd2: Scd2
    references: tuple[Reference, ...] = ()

    @model_validator(mode="after")
    def _personal_data_has_a_purpose(self) -> EntityContract:
        if self.personal_data and not (self.purpose or "").strip():
            raise ValueError(
                f"entity '{self.id}' holds personal data and declares no purpose. GDPR "
                "Art. 5(1)(b) requires personal data to be collected for specified, explicit "
                "and legitimate purposes, and a purpose that is not written down is not "
                "specified. The contract does not load."
            )
        return self

    @model_validator(mode="after")
    def _a_key_does_not_change(self) -> EntityContract:
        overlap = sorted(set(self.key) & set(self.scd2.tracked))
        if overlap:
            raise ValueError(
                f"entity '{self.id}' tracks {overlap} as changing attributes and also uses "
                "them as key fields. A history keyed on something that changes is two "
                "entities wearing one name, and every point-in-time resolution against it "
                "silently returns whichever version sorts first."
            )
        return self

    @model_validator(mode="after")
    def _references_are_resolvable_fields(self) -> EntityContract:
        available = set(self.key) | set(self.scd2.tracked)
        dangling = sorted(
            reference.via for reference in self.references if reference.via not in available
        )
        if dangling:
            raise ValueError(
                f"entity '{self.id}' references another entity via {dangling}, which is "
                "neither a key field nor a tracked attribute — so there is nothing to join on"
            )
        return self
