"""**The third decision** — the settlement path, and the contract nothing was holding it to.

`contracts/decisions/settlement_publication.yaml` has existed since before the first line of
settlement code. Nothing referenced it. The totals were computed, the restatements were written,
and the document declaring what that path is allowed to do sat beside them unread — which is the
exact failure the contract layer exists to prevent, arriving from the direction nobody watches:
not a contract violated, a contract ignored.

The interesting property is not that settlement works. It is that **its safe state is the
opposite of curtailment's, and that has to be structural.**

Doctrine 1 says the safe state is a conservative deterministic action, *not* silence — because a
substation keeps heating while nobody decides. Settlement is the one path in this system where
that reasoning inverts: a number not yet stated has no physical consequence, and a wrong one
invoiced does. So its fallback is `withhold_and_restate`, and if an engine ever decided which of
those two behaviours to use at runtime rather than reading it off the contract, both decisions
would be one edit from being wrong in the direction nobody notices.

Seven cases. The first three are the contract holding the code; the last four are doctrine 4 —
a correction never erases what was previously stated.
"""

from __future__ import annotations

from evals.scoring import Case, first_problem, require
from watermark.contracts import load
from watermark.core.records import METER_INTERVAL, SETTLEMENT_GRAIN
from watermark.core.time import Duration, Instant
from watermark.core.watermarks import WatermarkStatus
from watermark.core.windows import WindowResult
from watermark.settlement.restatement import compare, net_delta_wh
from watermark.settlement.totals import settle

CONTRACT = "settlement_publication"
HOUR = Instant.from_iso("2026-03-14T09:00:00Z")

#: The legacy head-end is three days behind by design. The contract's horizon has to cover that
#: with room, or the platform publishes totals it knows will move.
HEAD_END_LAG = Duration.of_seconds(3 * 24 * 60 * 60)


def _result(
    interval: int, energy: int, *, revision: int = 0, supersedes: int | None = None
) -> WindowResult:
    start = HOUR.plus(Duration.of_millis(interval * METER_INTERVAL.millis))
    return WindowResult(
        meter_id="M00001",
        interval_start=start,
        energy_wh=energy,
        readings=1,
        duplicates_suppressed=0,
        corrections_absorbed=0,
        closed_at=start.plus(METER_INTERVAL),
        watermark_status=WatermarkStatus.ADVANCING,
        idle_partitions=(),
        first_seen_at=start,
        revision=revision,
        supersedes=supersedes,
        restatement_cause="late batch from the head-end" if supersedes is not None else None,
    )


# ── the contract holding the code ────────────────────────────────────────────


def the_contract_is_loaded_at_all() -> str:
    """The case that would have caught this. A contract nothing loads is a document."""
    contracts = load()
    return first_problem(
        require(
            CONTRACT in contracts.decisions,
            f"`{CONTRACT}` is not in the loaded contract set. Three decision contracts are "
            f"declared and this harness is the only thing that reads the third.",
        ),
    )


def silence_is_the_fallback_here_and_the_contract_says_so() -> str:
    """The inversion of doctrine 1, read off the contract rather than assumed.

    Curtailment's fallback *acts* — it throttles, because the substation does not stop heating
    while nobody decides. Settlement's withholds. Both are the conservative choice; which one is
    conservative is a property of whether the action has a physical consequence, and that is a
    fact about the decision, not about the engine deciding it.
    """
    contracts = load()
    settlement = contracts.decisions[CONTRACT]
    curtailment = contracts.decisions["curtailment"]

    return first_problem(
        require(
            settlement.fallback.id == "withhold_and_restate",
            f"the settlement fallback is `{settlement.fallback.id}`. Publishing a provisional "
            f"number under fallback is the one thing this path must not do: it invoices.",
        ),
        require(
            "publish" not in settlement.fallback.permitted_actions,
            "the settlement fallback may publish. A fallback that publishes has stated a number "
            "computed without the data that was late, and doctrine 4 then requires restating it "
            "— so the fallback creates the correction it exists to avoid.",
        ),
        require(
            curtailment.fallback.permitted_actions != settlement.fallback.permitted_actions,
            "both fallbacks permit the same actions. The whole argument of ADR-0001 is that "
            "these two paths fail in opposite directions; identical fallbacks mean one of them "
            "is wrong and the contracts can no longer say which.",
        ),
    )


def the_fallback_needs_neither_a_model_nor_a_fresh_feature() -> str:
    """A fallback that reads the feature store is unavailable exactly when it is needed."""
    settlement = load().decisions[CONTRACT]
    return first_problem(
        require(
            not settlement.fallback.uses_model,
            "the settlement fallback uses a model. The fallback exists for the case where the "
            "model path cannot run.",
        ),
        require(
            not settlement.fallback.uses_features,
            "the settlement fallback reads features. It would then be unavailable in exactly "
            "the conditions the primary path is — the one property ADR-0001 requires it not to "
            "have.",
        ),
        require(
            settlement.model is None and not settlement.features,
            "the settlement path declares a model or features. There is exactly one correct "
            "hourly total and it is arithmetic; a model step here would be an anti-pattern with "
            "repair logic underneath it.",
        ),
    )


def the_horizon_covers_the_lateness_it_was_written_for() -> str:
    """Four days, because the head-end is three behind. A horizon shorter than the lag is a
    promise to publish totals the platform already knows will move."""
    settlement = load().decisions[CONTRACT]
    return require(
        settlement.horizon.millis > HEAD_END_LAG.millis,
        f"the horizon is {settlement.horizon.millis // 1000}s and the head-end is "
        f"{HEAD_END_LAG.millis // 1000}s behind by design. Every total inside that gap is "
        f"published knowing it will restate.",
    )


# ── doctrine 4: a correction never erases what was previously stated ─────────


def an_hour_settles_from_its_own_intervals_only() -> str:
    """The calibration case. Everything below is about corrections; this is the easy half."""
    totals = settle([_result(index, 100 + index) for index in range(4)])
    return first_problem(
        require(len(totals) == 1, f"four intervals of one hour settled into {len(totals)} hours"),
        require(
            totals[0].energy_wh == 100 + 101 + 102 + 103,
            f"the hour totals {totals[0].energy_wh}, not the sum of its intervals",
        ),
        require(
            totals[0].intervals == SETTLEMENT_GRAIN.millis // METER_INTERVAL.millis,
            f"the hour reports {totals[0].intervals} intervals, not a full hour of them",
        ),
    )


def a_late_interval_restates_and_the_prior_value_survives() -> str:
    """Doctrine 4, on the number that gets invoiced.

    The restatement is not "the new total". It is the previous total, the new one, the delta and
    what caused it — because somebody holding an invoice for the old figure needs all four, and
    an overwrite leaves them with a number that changed and no account of why.
    """
    before = settle([_result(index, 100) for index in range(3)])
    after = settle([*[_result(index, 100) for index in range(3)], _result(3, 250)])
    moved = compare(before, after)

    if len(moved) != 1:
        return f"one hour gained a late interval and {len(moved)} restatements were reported"
    restatement = moved[0]
    return first_problem(
        require(
            restatement.previous_energy_wh == 300,
            f"the restatement reports a previous value of {restatement.previous_energy_wh}. "
            f"The prior statement is what doctrine 4 preserves; without it a correction is an "
            f"overwrite with extra steps.",
        ),
        require(
            restatement.new_energy_wh == 550,
            f"the restated total is {restatement.new_energy_wh}, not the sum including the late "
            f"interval",
        ),
        require(
            restatement.delta_wh == 250,
            f"the delta is {restatement.delta_wh}. It is the number that reconciles the two "
            f"invoices and it is the one somebody is owed.",
        ),
    )


def a_restatement_that_is_still_short_says_so() -> str:
    """A total that moved *and* is still incomplete will move again.

    Reporting it as a plain correction is the difference between a correction and a correction
    somebody has to chase — they reconcile, file it, and it moves under them a second time.
    """
    before = settle([_result(index, 100) for index in range(2)])
    after = settle([_result(index, 100) for index in range(3)])
    moved = compare(before, after)

    if len(moved) != 1:
        return f"{len(moved)} restatements where one hour gained one of its two missing intervals"
    return require(
        not moved[0].now_complete,
        "an hour still missing an interval was reported complete. The next late arrival moves "
        "it again, after somebody has already reconciled it.",
    )


def a_settled_hour_that_did_not_move_is_not_a_restatement() -> str:
    """The other direction, and the one that erodes quietly.

    A restatement record per settled hour per run is a report nobody reads, and a report nobody
    reads is where the real correction hides. `net_delta_wh` over an unchanged period must be
    zero *because nothing moved*, not because the deltas cancelled.
    """
    totals = settle([_result(index, 100) for index in range(4)])
    moved = compare(totals, totals)
    return first_problem(
        require(
            not moved,
            f"{len(moved)} restatements reported for a period in which nothing changed",
        ),
        require(
            net_delta_wh(moved) == 0,
            "the net delta over an unchanged period is not zero",
        ),
    )


CASES: tuple[Case, ...] = (
    Case(
        "the_contract_is_loaded_at_all",
        "The case that would have caught this. Three decision contracts are declared; two were "
        "exercised, and the third was a document sitting beside the code that implements it.",
        the_contract_is_loaded_at_all,
    ),
    Case(
        "silence_is_the_fallback_here_and_the_contract_says_so",
        "ADR-0001 inverted. On a grid, silence is not safe; on an invoice it is the only safe "
        "thing. Which one applies is a fact about the decision, read off its contract.",
        silence_is_the_fallback_here_and_the_contract_says_so,
    ),
    Case(
        "the_fallback_needs_neither_a_model_nor_a_fresh_feature",
        "A fallback that depends on the thing that failed is not a fallback.",
        the_fallback_needs_neither_a_model_nor_a_fresh_feature,
    ),
    Case(
        "the_horizon_covers_the_lateness_it_was_written_for",
        "The head-end is three days behind by design. A shorter horizon is a promise to publish "
        "totals the platform already knows will move.",
        the_horizon_covers_the_lateness_it_was_written_for,
    ),
    Case(
        "an_hour_settles_from_its_own_intervals_only",
        "The calibration case. Refusing everything would satisfy every other case in this file.",
        an_hour_settles_from_its_own_intervals_only,
    ),
    Case(
        "a_late_interval_restates_and_the_prior_value_survives",
        "Doctrine 4 on the number that gets invoiced: the prior value, the delta and the cause "
        "are all recoverable, or the correction is an overwrite with extra steps.",
        a_late_interval_restates_and_the_prior_value_survives,
    ),
    Case(
        "a_restatement_that_is_still_short_says_so",
        "A total that moved and is still incomplete will move again. Saying so is the difference "
        "between a correction and one somebody has to chase.",
        a_restatement_that_is_still_short_says_so,
    ),
    Case(
        "a_settled_hour_that_did_not_move_is_not_a_restatement",
        "A restatement per hour per run is a report nobody reads, and that is where the real "
        "correction hides.",
        a_settled_hour_that_did_not_move_is_not_a_restatement,
    ),
)
