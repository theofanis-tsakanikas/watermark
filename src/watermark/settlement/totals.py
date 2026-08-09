"""Rolling fifteen-minute windows up into settled hours and balancing groups."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from watermark.core.records import METER_INTERVAL, SETTLEMENT_GRAIN
from watermark.core.time import Instant
from watermark.core.windows import WindowResult
from watermark.lineage.identity import LineageId, derive

#: How many metering intervals make one settlement hour. Derived rather than written as 4, so
#: that changing either grain cannot leave the two disagreeing.
INTERVALS_PER_HOUR = SETTLEMENT_GRAIN.millis // METER_INTERVAL.millis


@dataclass(frozen=True, slots=True)
class HourlyTotal:
    """One meter's settled hour.

    Carries its own completeness. A total built from three of four intervals is not a smaller
    total, it is a different kind of statement — and an invoice that cannot tell the two apart
    is an invoice nobody can defend.
    """

    meter_id: str
    hour_start: Instant
    energy_wh: int
    #: How many of the hour's intervals contributed. Fewer than `INTERVALS_PER_HOUR` means a
    #: gap: a meter that did not report, or readings quarantined and never restated.
    intervals: int
    #: The highest revision among the contributing intervals. Non-zero means this hour has
    #: already been restated at least once.
    revision: int
    #: True when a substation was excluded from the watermark while any contributing interval
    #: was published. The hole travels from the window all the way to the invoice.
    computed_with_idle_partition: bool
    lineage_id: LineageId

    @property
    def is_complete(self) -> bool:
        return self.intervals == INTERVALS_PER_HOUR

    def sort_key(self) -> tuple[int, str]:
        return (self.hour_start.epoch_millis, self.meter_id)


@dataclass(frozen=True, slots=True)
class BalancingGroupTotal:
    """One balancing group's settled hour — what actually goes to market settlement."""

    balancing_group: str
    hour_start: Instant
    energy_wh: int
    meters: int
    #: Meters whose hour was incomplete. Named, not counted: the question after a short total
    #: is always *which* meters, and a count sends somebody back to the data to find out.
    incomplete_meters: tuple[str, ...]
    computed_with_idle_partition: bool
    lineage_id: LineageId

    @property
    def is_complete(self) -> bool:
        return not self.incomplete_meters

    def sort_key(self) -> tuple[int, str]:
        return (self.hour_start.epoch_millis, self.balancing_group)


def _latest_per_interval(results: Iterable[WindowResult]) -> dict[tuple[str, int], WindowResult]:
    """The last word for each meter-interval.

    A restatement supersedes; keeping both would double-count the interval, which is the
    single arithmetic mistake this whole path exists to avoid.
    """
    latest: dict[tuple[str, int], WindowResult] = {}
    for result in results:
        key = (result.meter_id, result.interval_start.epoch_millis)
        held = latest.get(key)
        if held is None or result.revision > held.revision:
            latest[key] = result
    return latest


def settle(
    results: Iterable[WindowResult],
    lineage: Mapping[tuple[str, int, int], LineageId] | None = None,
) -> tuple[HourlyTotal, ...]:
    """Sum fifteen-minute windows into settled hours, newest revision winning.

    Emitted in content order — `(hour, meter)` — never in the order the windows were produced.
    Same rule as everywhere else: claim 2 is a claim about bytes.
    """
    ids = lineage or {}
    buckets: dict[tuple[str, int], list[WindowResult]] = {}
    for result in _latest_per_interval(results).values():
        hour = result.interval_start.floor_to(SETTLEMENT_GRAIN)
        buckets.setdefault((result.meter_id, hour.epoch_millis), []).append(result)

    totals = []
    for (meter_id, hour_millis), contributing in buckets.items():
        parents = [
            ids[(result.meter_id, result.interval_start.epoch_millis, result.revision)]
            for result in contributing
            if (result.meter_id, result.interval_start.epoch_millis, result.revision) in ids
        ]
        hour_start = Instant(hour_millis)
        totals.append(
            HourlyTotal(
                meter_id=meter_id,
                hour_start=hour_start,
                # A sum of integers is the same number in every order. A sum of floats is not,
                # and that alone would end claim 2 for the settlement path.
                energy_wh=sum(result.energy_wh for result in contributing),
                intervals=len(contributing),
                revision=max(result.revision for result in contributing),
                computed_with_idle_partition=any(result.idle_partitions for result in contributing),
                lineage_id=derive("hour", f"{meter_id}|{hour_start.to_iso()}", parents),
            )
        )
    return tuple(sorted(totals, key=HourlyTotal.sort_key))


def settle_groups(
    hours: Iterable[HourlyTotal], membership: Mapping[str, str]
) -> tuple[BalancingGroupTotal, ...]:
    """Sum settled hours into balancing groups.

    `membership` maps a meter to its group and is resolved point-in-time by the caller, not
    here: a meter that changed group mid-period belongs to whichever group it was in *at the
    hour being settled*, and answering that needs the SCD-2 history rather than a dictionary
    of today's answers. A meter with no membership is excluded and named, never bucketed into
    a default group — an unattributed megawatt-hour is a market position somebody did not take.
    """
    buckets: dict[tuple[str, int], list[HourlyTotal]] = {}
    for hour in hours:
        group = membership.get(hour.meter_id)
        if group is None:
            continue
        buckets.setdefault((group, hour.hour_start.epoch_millis), []).append(hour)

    totals = []
    for (group, hour_millis), members in buckets.items():
        hour_start = Instant(hour_millis)
        totals.append(
            BalancingGroupTotal(
                balancing_group=group,
                hour_start=hour_start,
                energy_wh=sum(member.energy_wh for member in members),
                meters=len(members),
                incomplete_meters=tuple(
                    sorted(member.meter_id for member in members if not member.is_complete)
                ),
                computed_with_idle_partition=any(
                    member.computed_with_idle_partition for member in members
                ),
                lineage_id=derive(
                    "group",
                    f"{group}|{hour_start.to_iso()}",
                    [member.lineage_id for member in members],
                ),
            )
        )
    return tuple(sorted(totals, key=BalancingGroupTotal.sort_key))
