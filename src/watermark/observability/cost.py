"""Estimating what an hour of this platform costs, and what a decision costs within it.

Every rate below is in **euro cents per hour**, integer, and carries the date it was read.
Integers because a cost that appears in a report should be the same number on every machine —
the same argument ADR-0004 makes about features, applied to money.

**These are estimates from a rate card, and the repository says so wherever they appear.**
Nothing here has been billed: `docs/DECISIONS.md` 15 puts the estate permanently out of scope
for application. An estimate presented as a measurement would be the same overclaim the rest of
this project spends its time refusing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Rates read from AWS public pricing for eu-central-1 on 2026-08-09, in euro cents per hour.
#: Rounded up to the cent, so an estimate errs high — a cost model that flatters the design is
#: a cost model nobody checks against a bill later.
DEFAULT_RATES: dict[str, int] = {
    # A Managed Flink KPU. The orchestration KPU is charged too, so the floor is n+1 — see
    # docs/AWS-CONSTRAINTS.md.
    "flink_kpu_hour": 12,
    # A provisioned Kinesis shard.
    "kinesis_shard_hour": 2,
    # The Feature Store online store, per feature group, at this write volume.
    "feature_store_online_hour": 18,
    # An ml.m5.large real-time endpoint.
    "endpoint_hour": 14,
    # Athena, per hour of the settlement queries at this data volume.
    "athena_hour": 3,
    # S3, Glue, logs and the rest — small, and lumped rather than itemised because itemising
    # them would suggest a precision this estimate does not have.
    "storage_and_glue_hour": 4,
}


@dataclass(frozen=True, slots=True)
class CostModel:
    """What is running, and for how long."""

    flink_kpus: int
    kinesis_shards: int
    feature_groups_online: int
    endpoints: int
    rates: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RATES))

    def cents_per_hour(self) -> int:
        # The orchestration KPU. Charged, easy to forget, and the reason a four-KPU application
        # is a five-KPU bill.
        kpus = self.flink_kpus + 1
        return (
            kpus * self.rates["flink_kpu_hour"]
            + self.kinesis_shards * self.rates["kinesis_shard_hour"]
            + self.feature_groups_online * self.rates["feature_store_online_hour"]
            + self.endpoints * self.rates["endpoint_hour"]
            + self.rates["athena_hour"]
            + self.rates["storage_and_glue_hour"]
        )


@dataclass(frozen=True, slots=True)
class CostReport:
    """An estimate, and the two per-unit figures that make it comparable."""

    cents_per_hour: int
    decisions: int
    meters: int
    hours: int

    @property
    def total_cents(self) -> int:
        return self.cents_per_hour * self.hours

    @property
    def cents_per_million_decisions(self) -> int:
        """Cost per million decisions, in cents.

        The unit was chosen after the first run printed zero. At 250,000 meters taking four
        decisions an hour, the cost of one decision is a fraction of a millicent — and a metric
        that is always zero is a metric nobody looks at. Per million is the granularity at
        which this system's economics are actually legible: it is what makes "the platform
        costs more idle than it does deciding" a number rather than an impression.
        """
        return self.total_cents * 1_000_000 // max(1, self.decisions)

    @property
    def millicents_per_meter_hour(self) -> int:
        return self.total_cents * 1000 // max(1, self.meters * self.hours)

    def summary(self) -> str:
        return (
            f"estimated €{self.total_cents / 100:.2f} for {self.hours}h "
            f"({self.cents_per_hour}c/h): {self.cents_per_million_decisions}c per million "
            f"decisions across {self.decisions:,} decisions, "
            f"{self.millicents_per_meter_hour} millicents per meter-hour across "
            f"{self.meters:,} meters. Estimated from a rate card, not read off a bill: this "
            "is what the design permits, and the estate that was stood up and destroyed is "
            "reconciled against it in docs/DECISIONS.md 17."
        )


def estimate(model: CostModel, decisions: int, meters: int, hours: int) -> CostReport:
    return CostReport(model.cents_per_hour(), decisions, meters, hours)
