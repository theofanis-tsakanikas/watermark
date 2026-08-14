"""The fixed cast: substations, meters, customers, and their SCD-2 histories.

Small on purpose — four substations and forty meters, not four hundred and two hundred and
fifty thousand. The pathologies are what the harnesses assert on, and every one of them is
present at this size; scale would add minutes to the suite and not one more proved statement.
`docs/AWS-CONSTRAINTS.md` is where the real volumes decide anything, and what they decide there
is shard counts, not correctness.

Nothing here is random. The cast is a function of nothing at all, so two runs — and two
machines, and a run three months from now — produce the same entities in the same order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from watermark.core.pit import History, Version
from watermark.core.time import Duration, Instant

#: The day everything happens on. A fixed date rather than "today": a generator that reads a
#: clock produces a different dataset every day, and a golden recording of it is a recording of
#: the afternoon it was captured.
DAY: Final = "2026-03-14"
DAY_START: Final = Instant.from_iso(f"{DAY}T00:00:00Z")
DAY_END: Final = DAY_START.plus(Duration.of_days(1))

#: The meter that changes customer at 10:00 — the scenario's "a meter changes customer".
REASSIGNED_METER: Final = "M00007"
#: The meter whose tariff changes at 14:30 — the scenario's "a tariff change mid-period".
#:
#: **Half past, and it was on the hour until the first gold layer was built.** 14:00 is a
#: settlement hour boundary, so no hour ever contained two tariffs: everything from 13:00 to
#: 14:00 was priced at the old rate and everything after at the new, and `settlement_priced`
#: would have given the same answer if it had priced at the hour instead of at the interval.
#: The whole reason that model resolves the tariff per fifteen-minute interval is a change that
#: falls *inside* an hour, and the case as declared could not produce one.
#:
#: `pipelines/dbt/tests/priced_hours_cover_every_settled_hour.sql` is what said so, on its first
#: run against a real gold layer: no hour in the whole settlement crossed a boundary, which is
#: indistinguishable from a point-in-time join that has collapsed.
RETARIFFED_METER: Final = "M00019"

SUBSTATIONS: Final = ("SUB-01", "SUB-02", "SUB-03", "SUB-04")

#: The substation that goes quiet for forty minutes — claim 1's sharpest case. Named here so
#: that the generator, the harness and anybody reading the repository agree on which one it is
#: without grepping for a magic string.
IDLE_SUBSTATION: Final = "SUB-03"

#: How many meters hang off each substation.
METERS_PER_SUBSTATION: Final = 10

#: Positions within a substation that belong to the retrying cohort.
_RETRYING_POSITIONS: Final = (2, 7)
#: The position whose meter has deliberate evidence gaps, and which intervals it skips.
_GAP_POSITION: Final = 5
_GAP_INTERVALS: Final = (41, 42)
#: The legacy head-end serves the first few meters of one substation.
_LEGACY_SUBSTATION: Final = "SUB-04"
_LEGACY_POSITIONS: Final = 3
#: Devices whose clocks are wrong by more than any tolerance would allow.
_THREE_HOURS_FAST: Final = 13
_FORTY_FIVE_MINUTES_FAST: Final = 27
#: Above this, skew is not drift — it is a wrong clock.
SKEW_TOLERANCE_MS: Final = 5 * 60 * 1000


@dataclass(frozen=True, slots=True)
class Meter:
    """One device, and everything about it that decides how it misbehaves."""

    meter_id: str
    substation_id: str
    #: Which payload shape it emits. Three generations are on the wire at once.
    firmware: str
    #: Whether this meter belongs to the cohort that retries and duplicates.
    duplicates: bool
    #: Milliseconds its clock is ahead of reality. Small for most; past tolerance for two.
    skew_ms: int
    #: Whether its readings are delivered by the legacy head-end three days late.
    late_batch: bool
    #: Intervals this meter simply does not report. The deliberate evidence gaps, so that an
    #: abstention or fallback count can be asserted exactly rather than approximately.
    missing_intervals: tuple[int, ...]
    #: A deprivation proxy, 0 to 9. Not used to generate consumption directly; it is what the
    #: phase 3 bias analysis has to look for a correlation *with*, and putting it in the cast
    #: rather than inventing it later is what stops the analysis being fitted to its own answer.
    area_decile: int


def _meter(index: int) -> Meter:
    substation = SUBSTATIONS[index // METERS_PER_SUBSTATION]
    position = index % METERS_PER_SUBSTATION
    return Meter(
        meter_id=f"M{index:05d}",
        substation_id=substation,
        # A fleet mid-rollout: a third on each generation, deterministically assigned.
        firmware=("fw1", "fw2", "fw3")[index % 3],
        # The retrying cohort is one firmware generation, which is what makes "which firmware
        # duplicates?" a question with an answer — and roughly 2% of readings, per the scenario.
        duplicates=index % 3 == 0 and position in _RETRYING_POSITIONS,
        skew_ms=_skew_for(index),
        # The legacy head-end serves one substation. A late batch that was scattered evenly
        # across the fleet would restate every total by a little; concentrated, it restates a
        # few by a lot, which is the shape a real head-end migration has.
        late_batch=substation == _LEGACY_SUBSTATION and position < _LEGACY_POSITIONS,
        missing_intervals=_GAP_INTERVALS if position == _GAP_POSITION else (),
        area_decile=(index * 7) % 10,
    )


def _skew_for(index: int) -> int:
    """Clock skew per meter, including two devices beyond any tolerance.

    Most meters drift by seconds. Two are wrong by hours, which is the case that must be
    quarantined with a reason rather than clamped — clamping puts a real measurement in the
    wrong interval and leaves nothing anywhere to notice.
    """
    if index == _THREE_HOURS_FAST:
        return 3 * 60 * 60 * 1000  # three hours into the future
    if index == _FORTY_FIVE_MINUTES_FAST:
        return 45 * 60 * 1000  # forty-five minutes into the future
    return ((index % 7) - 3) * 1500  # -4.5s to +4.5s, ordinary drift


METERS: Final[tuple[Meter, ...]] = tuple(
    _meter(index) for index in range(len(SUBSTATIONS) * METERS_PER_SUBSTATION)
)

#: The two meters whose clocks are past tolerance. Derived from the cast rather than restated,
#: so a change to `_skew_for` cannot leave the harness asserting a number that is no longer true.
SKEWED_METERS: Final = tuple(
    meter.meter_id for meter in METERS if meter.skew_ms > SKEW_TOLERANCE_MS
)

#: The meters the legacy head-end delivers late, and whose totals therefore restate.
LATE_BATCH_METERS: Final = tuple(meter.meter_id for meter in METERS if meter.late_batch)

#: The retrying cohort.
DUPLICATING_METERS: Final = tuple(meter.meter_id for meter in METERS if meter.duplicates)

#: Meters with deliberate evidence gaps, and how many intervals each is missing.
GAP_METERS: Final = tuple(meter.meter_id for meter in METERS if meter.missing_intervals)


def substation_limits() -> History:
    """Thermal limits, changing at midday — the seasonal change, compressed into one day.

    A limit that never moves would let a point-in-time bug pass unnoticed: every resolution
    would return the same answer whether or not it asked the right question.
    """
    noon = Instant.from_iso(f"{DAY}T12:00:00Z")
    versions = []
    for position, substation in enumerate(SUBSTATIONS):
        winter = 400_000 + position * 25_000
        versions.append(Version(substation, DAY_START, noon, {"limit_w": str(winter)}))
        versions.append(Version(substation, noon, None, {"limit_w": str(winter + 50_000)}))
    return History.of("substation_limit", versions)


def meter_assignments() -> History:
    """Which customer each meter belongs to.

    `M00007` changes customer at 10:00. It is the scenario's *"a meter changes customer"*, and
    it is the case that separates a point-in-time join from a join: readings before 10:00
    belong to one customer and readings after belong to another, and resolving them all against
    the current assignment prices the morning to the wrong person.
    """
    changeover = Instant.from_iso(f"{DAY}T10:00:00Z")
    versions = []
    for meter in METERS:
        customer = f"C{meter.meter_id[1:]}"
        if meter.meter_id == REASSIGNED_METER:
            versions.append(
                Version(meter.meter_id, DAY_START, changeover, {"customer_id": customer})
            )
            versions.append(
                Version(meter.meter_id, changeover, None, {"customer_id": f"{customer}-NEW"})
            )
        else:
            versions.append(Version(meter.meter_id, DAY_START, None, {"customer_id": customer}))
    return History.of("meter_assignment", versions)


#: How many balancing groups the fleet is split across.
#:
#: Market settlement nets within a group, so the number matters to the arithmetic and not only
#: to the shape: one group makes `settlement_balancing_group` a rename of `settlement_hour` and
#: proves nothing about the join. Four is enough that a meter changing customer can change group.
BALANCING_GROUPS: Final = ("BG-NORTH", "BG-SOUTH", "BG-EAST", "BG-WEST")


def customers() -> History:
    """The customer reference data — balancing group and postcode area, SCD-2.

    **What is real here and what is standing in.** The cast fixes which meter belongs to which
    customer, and that is the fact `meter_assignments` carries. A balancing group and a postcode
    area are properties of the *customer*, held in an operational CRM this repository does not
    model — so they are derived deterministically from the customer id rather than invented per
    run. A generator that produced a different postcode each time would make settlement
    irreproducible, which is the one thing settlement may not be.

    The postcode area is coarse on purpose. `docs/SCENARIO.md` names proxy discrimination as the
    live risk in this system, and a full postcode is a location fine enough to identify a
    household — carrying one into a training set would be building the hazard the bias analysis
    exists to measure. The area is the first outward segment and nothing more.

    Single-version, with one exception: the customer who takes over `M00007` at 10:00 is a
    different customer and sits in a different group, so a meter that changes customer also
    changes balancing group. That is what makes the point-in-time join in
    `settlement_balancing_group` a join that can be got wrong.
    """
    versions = []
    for index, meter in enumerate(METERS):
        customer = f"C{meter.meter_id[1:]}"
        group = BALANCING_GROUPS[index % len(BALANCING_GROUPS)]
        area = f"AR{index % 17:02d}"
        versions.append(
            Version(
                customer,
                DAY_START,
                None,
                {"balancing_group": group, "postcode_area": area},
            )
        )
        if meter.meter_id == REASSIGNED_METER:
            # The successor, in a different group deliberately. Same meter, same day, two market
            # positions — and only a point-in-time resolution puts each hour in the right one.
            versions.append(
                Version(
                    f"{customer}-NEW",
                    DAY_START,
                    None,
                    {
                        "balancing_group": BALANCING_GROUPS[(index + 1) % len(BALANCING_GROUPS)],
                        "postcode_area": area,
                    },
                )
            )
    return History.of("customer", versions)


def tariffs() -> History:
    """Tariffs, with one meter changing mid-period.

    `M00019` moves from the standard tariff to a time-of-use tariff at 14:30 — the scenario's
    *"a tariff change mid-period"*, and half past rather than on the hour so that a settlement
    hour genuinely straddles it. See `RETARIFFED_METER` for what the on-the-hour version failed
    to exercise.
    """
    change = Instant.from_iso(f"{DAY}T14:30:00Z")
    versions = []
    for meter in METERS:
        if meter.meter_id == RETARIFFED_METER:
            versions.append(
                Version(
                    meter.meter_id,
                    DAY_START,
                    change,
                    {"tariff_code": "STD-01", "unit_price_cents_per_kwh": "24"},
                )
            )
            versions.append(
                Version(
                    meter.meter_id,
                    change,
                    None,
                    {"tariff_code": "TOU-02", "unit_price_cents_per_kwh": "31"},
                )
            )
        else:
            versions.append(
                Version(
                    meter.meter_id,
                    DAY_START,
                    None,
                    {"tariff_code": "STD-01", "unit_price_cents_per_kwh": "24"},
                )
            )
    return History.of("tariff", versions)
