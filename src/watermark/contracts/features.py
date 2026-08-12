"""The feature contract. One definition, compiled two ways.

A feature contract declares what a feature means, over what window and grain, how stale is too
stale, what it is collected for, and how it is represented. Two of those refuse to load when
absent, and both refusals are the point of the file.

**A feature with no freshness budget does not load.** Claim 4 says a stale feature is never
served. That is mechanical only if every feature has a budget; the moment one can be added
without one, claim 4 becomes a matter of remembering, and the feature that gets forgotten is
the one added at the end of a long afternoon.

**A feature with no declared purpose does not load.** GDPR Art. 5(1)(b), the same rule the
entity contracts carry. A purpose that is not written down is not specified.

And one that is subtler than either, from ADR-0004: **a feature whose value cannot be
represented exactly in one of the Feature Store's three types must declare a scale.** There is
no decimal type. Kilowatt-hours as a double compared against `decimal(18,3)` in Iceberg differ
in the last bits by construction, and the only honest answers are a comparison tolerance or an
exact representation. Doctrine 7 says the parity door has no key, and a tolerance is a key — so
it is the representation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from watermark.core.time import Duration

#: The three types SageMaker Feature Store has. Verified 2026-08-09; see docs/AWS-CONSTRAINTS.md.
FeatureType = Literal["String", "Integral", "Fractional"]


class Window(BaseModel):
    """The span a feature aggregates over, and the grain it is emitted at."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    length_seconds: int = Field(gt=0)
    grain_seconds: int = Field(gt=0)

    @property
    def length(self) -> Duration:
        return Duration.of_seconds(self.length_seconds)

    @property
    def grain(self) -> Duration:
        return Duration.of_seconds(self.grain_seconds)

    @model_validator(mode="after")
    def _the_window_is_a_whole_number_of_grains(self) -> Window:
        if self.length_seconds % self.grain_seconds:
            raise ValueError(
                f"a {self.length_seconds}s window does not divide into {self.grain_seconds}s "
                "grains. A partial grain at the edge is a value that means something different "
                "from every other value of the same feature, and nothing downstream can tell."
            )
        return self


class FeatureContract(BaseModel):
    """One feature, defined once and compiled into two independent mechanisms (ADR-0004)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    owner: str

    #: The entity this is a feature *of*. It is the Feature Store's record identifier and the
    #: group-by of the offline query, which is exactly why it is declared once rather than
    #: written twice.
    entity: str
    entity_key: str

    window: Window

    #: How stale a value may be before it must not be served. Required, with no default —
    #: see the module docstring. Claim 4 is mechanical only because this cannot be omitted.
    freshness_budget_seconds: int = Field(gt=0)

    #: GDPR Art. 5(1)(b). Required whenever the feature is derived from personal data.
    personal_data: bool
    purpose: str | None = None

    value_type: FeatureType
    #: The power of ten the value is multiplied by to reach an exact integer. `1` for a
    #: quantity that is already whole; `1000` for kilowatt-hours held as watt-hours.
    scale: int = 1

    #: The aggregation, as a name both compilers understand. A closed vocabulary rather than an
    #: expression: an expression would be a second small language, and the two compilers would
    #: implement it differently, which is the failure mode claim 3 exists to catch happening
    #: inside the mechanism designed to catch it.
    aggregation: Literal["sum", "mean", "max", "min", "count", "last"]
    source_table: str
    source_column: str
    event_time_column: str
    #: The **ingestion** axis, and it is declared rather than assumed.
    #:
    #: `as_of_sql` hardcoded `ingest_time`, which is the column `substation_telemetry` happens
    #: to have and `meter_interval` does not — that one records when the winning copy of a
    #: reading was first seen, under the name `first_seen_at`. Athena answered
    #: `COLUMN_NOT_FOUND` the first time the compiled query was executed against the table its
    #: own contract names.
    #:
    #: A bitemporal query without this axis is not merely missing a filter: a late arrival then
    #: changes what the query returns for an instant that has already been served, and the
    #: parity harness reports a divergence about a reading nobody had when the decision was
    #: taken. So it is required, with no default — a contract that does not say which column
    #: records ingestion cannot be resolved as-of anything.
    ingest_time_column: str

    @property
    def freshness_budget(self) -> Duration:
        return Duration.of_seconds(self.freshness_budget_seconds)

    @model_validator(mode="after")
    def _personal_data_has_a_purpose(self) -> FeatureContract:
        if self.personal_data and not (self.purpose or "").strip():
            raise ValueError(
                f"feature '{self.id}' is derived from personal data and declares no purpose. "
                "GDPR Art. 5(1)(b) requires a specified purpose, and one that is not written "
                "down is not specified. The contract does not load."
            )
        return self

    @model_validator(mode="after")
    def _an_inexact_value_declares_a_scale(self) -> FeatureContract:
        """ADR-0004, enforced at load time.

        A `Fractional` feature is an IEEE-754 double. Compared against the same quantity held
        as a decimal in Iceberg it differs in the last bits, and no care in the aggregation
        prevents it. The only two answers are a comparison tolerance and an exact
        representation; doctrine 7 says the parity door has no key, and a tolerance is a key.
        """
        if self.value_type == "Fractional":
            raise ValueError(
                f"feature '{self.id}' declares Fractional. There is no decimal type in the "
                "Feature Store, so a Fractional value is a double — and a double compared "
                "against the same quantity in Iceberg differs in the last bits by "
                "construction. Declare Integral with a scale (ADR-0004): a tolerance in the "
                "parity comparison is a key to the one door that has none."
            )
        if self.value_type == "Integral" and self.scale < 1:
            raise ValueError(f"feature '{self.id}': a scale below 1 is not a scale")
        if self.value_type == "String" and self.scale != 1:
            raise ValueError(f"feature '{self.id}': a String feature has no scale")
        return self

    @model_validator(mode="after")
    def _the_budget_is_not_longer_than_the_window(self) -> FeatureContract:
        """A budget longer than the window can never be exceeded.

        It looks like a generous setting and it is a disabled control: the feature is
        recomputed every grain, so a value older than the window is a value the pipeline has
        already stopped producing. Claim 4 would then be true and vacuous.
        """
        if self.freshness_budget_seconds > self.window.length_seconds:
            raise ValueError(
                f"feature '{self.id}' has a {self.freshness_budget_seconds}s freshness budget "
                f"over a {self.window.length_seconds}s window. The budget can never be "
                "exceeded, so claim 4 would hold vacuously for this feature."
            )
        return self
