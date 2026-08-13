"""Every case the cast declares must be observable in the output, and none may go unchecked.

**Why this harness exists, and what it is a reaction to.** The other seven harnesses each prove
one *claim* — that a window does not close early, that a replay is identical, that a feature is
never served stale. Every one of them builds the situation it needs, which is right: a claim is a
statement about behaviour and the cleanest way to test behaviour is to construct it.

The cost is that nothing was checking the other direction. `data/cast.py` declares a fixed cast
with deliberate defects in it — a substation that goes quiet, meters that retry, two devices with
wrong clocks, a meter that changes customer, a meter whose tariff moves, a meter with missing
intervals — and an audit found that two of those were exercised by *nothing at all*, anywhere in
the repository. The evidence gap had no consumer. Neither did the tariff change. They had been
declared, commented, and never once observed.

A case that nothing reads cannot fail. That is worse than a case that fails, because it looks
handled — in a document, indefinitely.

**So this harness runs the generated day through the same runner every claim uses and asks a
different question: for each defect the cast declares, did the system actually see it?** Not "can
the system handle a quiet substation" — the watermark harness answers that — but "did SUB-03's
forty minutes of silence reach the output of the day this repository actually ships".

**The last case is the one that keeps the rest honest.** `every_declared_cohort_is_checked`
enumerates the cast's cohorts and fails if one has no case here. Adding a meter with a new defect
to `data/cast.py` and forgetting to check it is exactly how the two gaps above happened, and it
is the failure a fixed list of tests cannot catch — the list looks complete because it is the
list.
"""

from __future__ import annotations

from typing import Final

from data import cast
from data.generate import generate
from data.telemetry import OVERLOADED_SUBSTATION, readings
from evals.scoring import Case, require
from watermark.core.quarantine import Reason
from watermark.core.records import METER_INTERVAL
from watermark.core.time import Duration
from watermark.core.watermarks import WatermarkStatus
from watermark.runner import Arrival, run

#: The generated day, driven through the core once. Module-level because every case reads it and
#: a day per case would be twelve runs of the same computation.
_RESULT: Final = run(
    [
        Arrival(delivery.raw, delivery.ingest_time, delivery.source, delivery.partition)
        for delivery in generate()
    ],
    cast.SUBSTATIONS,
)


def _published_intervals(meter_id: str) -> set[int]:
    """Which interval starts this meter has a published total for, at any revision."""
    return {
        result.interval_start.epoch_millis
        for result in (*_RESULT.published, *_RESULT.restated, *_RESULT.confirmed)
        if result.meter_id == meter_id
    }


def the_evidence_gap_leaves_a_hole() -> str:
    """A meter that sent nothing for two intervals must have no total for them.

    **The case that had no consumer.** `data/cast.py` gives one meter per substation two missing
    intervals — "a deliberate evidence gap: this reading does not exist at all", says the
    generator — and until this harness nothing in the repository looked. `settlement_hour`
    computes `is_complete` and `watermark.settlement.totals` is tested for it, but both were
    tested against constructed fixtures rather than against the meter the cast actually declares.

    The distinction matters because the failure it guards against is silent. A window with no
    readings does not publish a zero; it publishes nothing. An hour built from three intervals is
    not a smaller total, it is a different kind of statement — and a system that filled the hole
    with an interpolation, or that published a partial hour as complete, would look exactly like
    this one from every aggregate the capture asserts.
    """
    if not cast.GAP_METERS:
        return "the cast declares no meter with missing intervals, so this case is unreachable"

    meter = cast.GAP_METERS[0]
    published = _published_intervals(meter)
    missing = {
        cast.DAY_START.plus(Duration.of_minutes(15 * index)).epoch_millis
        for index in cast._GAP_INTERVALS
    }
    leaked = sorted(missing & published)
    if leaked:
        return (
            f"{meter} has a published total for {len(leaked)} interval(s) it never sent a reading "
            f"for. A gap must stay a gap: an hour built from three intervals is a different kind "
            f"of statement, not a smaller total"
        )
    return require(
        len(published) > 0,
        f"{meter} published nothing at all, so the gap is indistinguishable from the meter "
        f"being absent — the case needs a meter that reports and then does not",
    )


def a_wrong_clock_is_quarantined_by_name() -> str:
    """The two devices past tolerance are refused, and refused for the clock.

    `evals/watermark/` proves a skewed clock cannot close the grid early. This asks the narrower
    question the cast declares: are *these two meters*, the ones `data/cast.py` names, actually
    refused in the day this repository ships? A tolerance that quietly widened would leave that
    harness green — it builds its own reading — and would let the fleet's real skew through.
    """
    if not cast.SKEWED_METERS:
        return "the cast declares no meter past the skew tolerance"

    refused = {
        quarantined.payload
        for quarantined in _RESULT.quarantined
        if quarantined.reason is Reason.CLOCK_SKEW_FUTURE
    }
    unrefused = [meter for meter in cast.SKEWED_METERS if not any(meter in p for p in refused)]
    return require(
        not unrefused,
        f"{unrefused} are declared past the skew tolerance and none of their readings was "
        f"quarantined for a clock. Either the tolerance moved or the skew did",
    )


def the_retrying_cohort_is_deduplicated() -> str:
    """At-least-once delivery is absorbed, and the count says so.

    A duplicate that is silently dropped and a duplicate that is suppressed and counted look the
    same in every total. The difference appears only in `duplicates_suppressed`, which is why it
    is a column rather than a log line — and why this asserts the count rather than the total.
    """
    if not cast.DUPLICATING_METERS:
        return "the cast declares no retrying meter"

    suppressed = {
        result.meter_id: result.duplicates_suppressed
        for result in (*_RESULT.published, *_RESULT.restated, *_RESULT.confirmed)
        if result.duplicates_suppressed > 0
    }
    missed = [meter for meter in cast.DUPLICATING_METERS if meter not in suppressed]
    return require(
        not missed,
        f"{len(missed)} of {len(cast.DUPLICATING_METERS)} retrying meters had no duplicate "
        f"suppressed anywhere in the day: {missed[:3]}",
    )


def the_late_batch_restates_and_says_what_it_replaced() -> str:
    """Doctrine 4, against the meters the head-end actually serves."""
    if not cast.LATE_BATCH_METERS:
        return "the cast declares no meter served by the legacy head-end"

    restated = {result.meter_id for result in _RESULT.restated}
    covered = [meter for meter in cast.LATE_BATCH_METERS if meter in restated]
    if not covered:
        return (
            f"none of the {len(cast.LATE_BATCH_METERS)} late-batch meters restated anything. "
            f"Either the file never arrived or a correction overwrote instead of superseding"
        )
    unnamed = [result for result in _RESULT.restated if result.supersedes is None]
    return require(
        not unnamed,
        f"{len(unnamed)} restatements do not say what they replaced, which is doctrine 4",
    )


def the_quiet_substation_holds_the_watermark_back() -> str:
    """SUB-03's forty minutes of silence must be visible in the day, not only in a fixture.

    **Held back, not idle, and the first version of this case asserted the wrong one.** It looked
    for `SUB-03` in the `idle_partitions` of a published row and found nothing, because forty
    minutes is *less* than `idle_after`, which is an hour of event time. A partition that goes
    quiet for less than the exclusion threshold does not get excluded — it pins the watermark,
    which is the whole point of the case: the grid stops closing windows and says whose fault it
    is.

    The two states are easy to confuse and mean opposite things. `advancing_with_idle` is "we
    gave up on that partition and closed anyway, and every total from here carries a hole".
    `held_back` is "we have not given up and nothing is closing". Asserting the first would have
    passed on a system that abandoned a substation after forty minutes, which is precisely the
    behaviour `idle_after` exists to postpone.
    """
    culprits = {
        tick.holding_back for tick in _RESULT.ticks if tick.status is WatermarkStatus.HELD_BACK
    }
    return require(
        cast.IDLE_SUBSTATION in culprits,
        f"{cast.IDLE_SUBSTATION} goes quiet for forty minutes by declaration and never held the "
        f"watermark back. Culprits named across the day: "
        f"{sorted(c for c in culprits if c) or 'none'}",
    )


def the_day_exercises_every_watermark_condition() -> str:
    """The generated day must reach each state claim 1 distinguishes, or the states are theory.

    Four conditions, and each answers a different operational question: `advancing` is healthy;
    `held_back` is "nothing is closing and here is who"; `advancing_with_idle` is "we closed
    anyway and every total from here has a hole"; `starved` is "nothing is arriving at all".
    `evals/watermark/` proves each one is *reachable* by constructing it. This asks whether the
    day this repository actually ships reaches them, which is a different question and the one
    that decays silently — a generator tuned for another reason can stop producing a state and
    every constructed harness stays green.

    `stalled` is not required here. It needs records arriving while the leader does not move, and
    whether the generated day contains such a stretch depends on arrival offsets that exist for
    other reasons; requiring it would make this case fail for something unrelated to the states.
    """
    reached = {tick.status for tick in _RESULT.ticks}
    wanted = {
        WatermarkStatus.ADVANCING,
        WatermarkStatus.HELD_BACK,
        WatermarkStatus.ADVANCING_WITH_IDLE,
        WatermarkStatus.STARVED,
    }
    missing = sorted(status.value for status in wanted - reached)
    return require(
        not missing,
        f"the generated day never reaches {missing}. Every one of those is a state a decision "
        f"path branches on, and a day that cannot produce it leaves the branch proved only "
        f"against a constructed fixture",
    )


def the_reassigned_meter_belongs_to_two_customers() -> str:
    """A point-in-time join, asked the question it exists for.

    The meter changes customer at 10:00. Resolving it against the *current* row would put the
    whole day on one side of that change — the classic route to a market position nobody took,
    and the one error `src/watermark/core/pit.py` has exactly one answer to.
    """
    history = cast.meter_assignments()
    before = history.attribute(cast.REASSIGNED_METER, cast.DAY_START, "customer_id")
    after = history.attribute(
        cast.REASSIGNED_METER, cast.DAY_END.minus(METER_INTERVAL), "customer_id"
    )
    return require(
        before is not None and after is not None and before != after,
        f"{cast.REASSIGNED_METER} resolves to {before!r} at the start of the day and {after!r} at "
        f"the end. The scenario declares it changes customer, so a join that returns one answer "
        f"for both is not point-in-time",
    )


def the_retariffed_meter_is_priced_at_two_rates() -> str:
    """The case that had no consumer anywhere in the repository.

    `docs/SCENARIO.md` declares "a tariff changes mid-period", `data/cast.py` builds the history,
    and until `gold.settlement_priced` was written the word `tariff` appeared in this repository
    only inside the docstrings of `pit.py`. This is the offline half of that: the SQL model runs
    only against an estate, and a case that can be checked on a laptop should be.
    """
    history = cast.tariffs()
    before = history.attribute(cast.RETARIFFED_METER, cast.DAY_START, "unit_price_cents_per_kwh")
    after = history.attribute(
        cast.RETARIFFED_METER, cast.DAY_END.minus(METER_INTERVAL), "unit_price_cents_per_kwh"
    )
    if before is None or after is None:
        return f"{cast.RETARIFFED_METER} has no tariff in force at one end of the day"
    return require(
        before != after,
        f"{cast.RETARIFFED_METER} is priced at {before} cents all day. The scenario declares a "
        f"tariff change mid-period, and an hour that straddles it must contain two rates",
    )


def the_substation_limit_moves_at_noon() -> str:
    """A limit that never moved would let a point-in-time bug pass unnoticed.

    Every resolution would return the same answer whether or not it asked the right question —
    which is the reason `cast.substation_limits` moves it at all, and the reason that reason
    needs a check.
    """
    limits = cast.substation_limits()
    morning = limits.attribute(cast.SUBSTATIONS[0], cast.DAY_START, "limit_w")
    evening = limits.attribute(cast.SUBSTATIONS[0], cast.DAY_END.minus(METER_INTERVAL), "limit_w")
    return require(
        morning is not None and evening is not None and morning != evening,
        f"{cast.SUBSTATIONS[0]}'s limit is {morning} at both ends of the day, so a decision taken "
        f"against the wrong version would resolve to the right number by accident",
    )


def one_substation_crosses_its_thermal_limit() -> str:
    """The curtailment case, and the reason the load profile is not a flat line.

    A profile that stays under every threshold makes every decision `release` and a broken
    comparison indistinguishable from a working one. A profile that is over on every substation
    tests the threshold exactly as poorly in the other direction, which is what the first version
    of the generator did at 140% everywhere.
    """
    over = {sample.substation_id for sample in readings() if sample.load_w > sample.limit_w}
    return require(
        over == {OVERLOADED_SUBSTATION},
        f"substations over their limit: {sorted(over) or 'none'}. Exactly one must be, and it "
        f"must be {OVERLOADED_SUBSTATION}: none makes every decision `release`, and all of them "
        f"makes `over the limit` the normal state",
    )


def every_declared_cohort_is_checked() -> str:
    """The case that keeps the others honest, and the one this harness was written for.

    **Two of the cast's declared defects were exercised by nothing at all** — the evidence gap and
    the tariff change — and an audit found them only because somebody went looking. A fixed list
    of tests cannot catch that: the list looks complete because it *is* the list.

    So the cohorts are enumerated from `data/cast.py` and matched against the cases above by
    name. A meter with a new kind of defect, added to the cast and not checked here, fails this
    harness rather than sitting unobserved. It is the same arrangement as `EXTERNAL` in
    `check_lakehouse_wiring.py`: the declaration is what makes an omission loud.
    """
    declared = {
        "GAP_METERS": cast.GAP_METERS,
        "SKEWED_METERS": cast.SKEWED_METERS,
        "DUPLICATING_METERS": cast.DUPLICATING_METERS,
        "LATE_BATCH_METERS": cast.LATE_BATCH_METERS,
    }
    covered = {
        "GAP_METERS": "the_evidence_gap_leaves_a_hole",
        "SKEWED_METERS": "a_wrong_clock_is_quarantined_by_name",
        "DUPLICATING_METERS": "the_retrying_cohort_is_deduplicated",
        "LATE_BATCH_METERS": "the_late_batch_restates_and_says_what_it_replaced",
    }

    unchecked = sorted(set(declared) - set(covered))
    if unchecked:
        return (
            f"the cast declares {unchecked} and no case in this harness observes them. A defect "
            f"nothing reads cannot fail, which is worse than one that does"
        )
    empty = sorted(name for name, members in declared.items() if not members)
    return require(
        not empty,
        f"{empty} is declared in the cast and contains no meter, so the case above it passes "
        f"over an empty set — which is the shape of a green check that proves nothing",
    )


CASES: Final = (
    Case(
        "the evidence gap leaves a hole",
        "A window with no readings publishes nothing. An hour built from three intervals is a "
        "different kind of statement, not a smaller total — and nothing in this repository was "
        "checking that the meter the cast declares actually produces one.",
        the_evidence_gap_leaves_a_hole,
    ),
    Case(
        "a wrong clock is quarantined, by name",
        "Two devices are past tolerance by declaration. A tolerance that quietly widened would "
        "leave the watermark harness green, because it builds its own reading.",
        a_wrong_clock_is_quarantined_by_name,
    ),
    Case(
        "the retrying cohort is deduplicated",
        "A duplicate silently dropped and one suppressed and counted look identical in every "
        "total. The difference is the count, which is why it is a column.",
        the_retrying_cohort_is_deduplicated,
    ),
    Case(
        "the late batch restates and says what it replaced",
        "Doctrine 4, against the meters the head-end actually serves rather than a fixture.",
        the_late_batch_restates_and_says_what_it_replaced,
    ),
    Case(
        "the quiet substation holds the watermark back",
        "Held back, not idle: forty minutes is less than `idle_after`, so the grid stops closing "
        "and names the culprit rather than abandoning it. Asserting the other state would pass "
        "on a system that gave up after forty minutes.",
        the_quiet_substation_holds_the_watermark_back,
    ),
    Case(
        "the day reaches every watermark condition",
        "Each state answers a different operational question. A generator tuned for another "
        "reason can stop producing one, and every constructed harness stays green.",
        the_day_exercises_every_watermark_condition,
    ),
    Case(
        "the reassigned meter belongs to two customers",
        "Resolving against the current row puts the whole day on one side of a change that "
        "happened at 10:00 — a market position nobody took.",
        the_reassigned_meter_belongs_to_two_customers,
    ),
    Case(
        "the retariffed meter is priced at two rates",
        "Declared in the scenario, built in the cast, and read by nothing at all until "
        "`gold.settlement_priced`. A case with no consumer cannot fail.",
        the_retariffed_meter_is_priced_at_two_rates,
    ),
    Case(
        "the substation limit moves at noon",
        "A limit that never moved would let every point-in-time resolution return the right "
        "answer by accident.",
        the_substation_limit_moves_at_noon,
    ),
    Case(
        "exactly one substation crosses its thermal limit",
        "None makes every curtailment decision `release`; all of them makes `over the limit` the "
        "normal state. Both test the threshold equally poorly.",
        one_substation_crosses_its_thermal_limit,
    ),
    Case(
        "every declared cohort is checked",
        "Two of the cast's defects were observed by nothing until an audit found them. A fixed "
        "list of tests cannot catch that, because the list looks complete by being the list.",
        every_declared_cohort_is_checked,
    ),
)
