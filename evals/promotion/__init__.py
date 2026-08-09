"""**Claim 5** — no model reaches an endpoint without passing the gates, and a refusal names why.

Nine cases. Eight are refusals and one is a promotion, and the promotion is not padding: a gate
that refuses everything satisfies every other case in this file and ships nothing.

Each refusal asserts the *reason code*, not that an exception was raised. "The gate said no" is
not evidence that the gate checked the thing it was supposed to — a model rejected for a
missing card when the real problem was its recall would look identical from the outside, and
somebody would fix the card.
"""

from __future__ import annotations

from data.labels import labels

from evals.scoring import Case, first_problem, require
from watermark.core.time import Instant
from watermark.models.bias import Subject, measure_proxy_discrimination
from watermark.models.promotion import (
    Approval,
    PromotionGate,
    PromotionRefused,
    Thresholds,
)
from watermark.models.train import Example, train_anomaly_scorer

AT = Instant.from_iso("2026-03-14T00:00:00Z")
SNAPSHOT = "iceberg-snapshot-8811923044"

#: Deliberately not the thresholds the current model passes. A gate whose numbers were chosen
#: after seeing the metrics is a gate that has never refused anything.
THRESHOLDS = Thresholds(
    min_precision_per_mille=600,
    min_recall_per_mille=800,
    max_unexplained_disparity_per_mille=200,
    max_precision_gap_per_mille=300,
)


def _run(*, on_ground_truth: bool = False):
    """Train the scorer, on the confirmations or on the truth.

    Both are used. The confirmations are what a real pipeline has; the truth is knowable here
    only because the data is synthetic, and its absence in production is exactly what makes the
    finding in `docs/BIAS-FINDING.md` invisible. Training on both is what lets this harness
    distinguish "the model is bad" from "the labels are".
    """
    population = labels()
    examples = [
        Example(
            item.meter_id,
            AT,
            (item.score,),
            int(item.truly_tampering if on_ground_truth else item.confirmed),
        )
        for item in population
    ]
    return train_anomaly_scorer(examples, SNAPSHOT, AT), population


def _bias(run, population, *, on_ground_truth: bool = False):
    subjects = [
        Subject(
            item.meter_id,
            item.deprivation_decile,
            item.score >= run.model.threshold,
            item.truly_tampering if on_ground_truth else item.confirmed,
            item.truly_tampering,
        )
        for item in population
    ]
    return measure_proxy_discrimination(subjects)


def _card(run):
    return run.model_card(
        intended_purpose="Rank meters for inspection. Never actuated automatically (claim 7).",
        hazard=(
            "Proxy discrimination through the inspection feedback loop. See docs/BIAS-FINDING.md."
        ),
    )


def _approval(**kwargs) -> Approval:
    return Approval(approver="r.papadopoulou", at=AT, **kwargs)


def _refusal_reason(*, on_ground_truth: bool = False, **overrides) -> str:
    run, population = _run(on_ground_truth=on_ground_truth)
    arguments = {
        "run": run,
        "bias": _bias(run, population, on_ground_truth=on_ground_truth),
        "approval": _approval(),
        "model_card": _card(run),
        "parity_holds": True,
    }
    arguments.update(overrides)
    gate = PromotionGate(arguments.pop("_thresholds", THRESHOLDS))
    try:
        gate.evaluate(**arguments)
    except PromotionRefused as refusal:
        return refusal.reason
    return "promoted"


def training_is_reproducible() -> str:
    """The same pinned snapshot yields the same metrics, and the same artefact digest.

    Claim 5 rests on it. A metric that moves between runs is a metric no threshold can be set
    against, and an artefact digest that moves makes a rollback a guess.
    """
    first, _ = _run()
    second, _ = _run()
    return first_problem(
        require(first.metrics == second.metrics, "two runs over one snapshot disagreed"),
        require(first.model.digest() == second.model.digest(), "the artefact digest moved"),
        require(first.data_digest == second.data_digest, "the training set digest moved"),
    )


def the_shipped_model_is_refused_for_the_finding() -> str:
    """The model trained on inspector confirmations **does not pass the gate.**

    That is the result, not a failure of the harness. `docs/BIAS-FINDING.md` measured precision
    at 1000/1000 in the most deprived tercile against 181/1000 in the least — not because the
    model understands one group better, but because every true case there was confirmed and
    almost none elsewhere was. The gate refuses it, and it refuses it for the label coverage
    rather than for anything about the model.

    Tuning the threshold until this promoted would have been the easy move and the dishonest
    one. What ships instead is a refusal with a reason and a document explaining it.
    """
    reason = _refusal_reason()
    return require(
        reason == "precision_gap",
        f"the model trained on confirmations was {reason!r} rather than refused for its "
        "precision gap. If the fixture has changed so that the finding no longer reproduces, "
        "update docs/BIAS-FINDING.md — do not relax the threshold.",
    )


def a_model_trained_on_unbiased_labels_is_promoted() -> str:
    """The calibration case, and the other half of the finding.

    Same model class, same thresholds, same population — labelled by ground truth instead of by
    the dispatch log. It promotes. That is what distinguishes "this model is bad" from "these
    labels are", and it is why the mitigation in `docs/BIAS-FINDING.md` is randomised
    inspection rather than a different algorithm.
    """
    reason = _refusal_reason(on_ground_truth=True)
    return require(
        reason == "promoted",
        f"a model trained on unbiased labels was refused for {reason!r}. Either the thresholds "
        "are unreachable — in which case nothing ever ships and the gate is decoration — or "
        "the labels are not the whole story and docs/BIAS-FINDING.md overstates the case.",
    )


def a_model_below_precision_is_refused_for_precision() -> str:
    strict = Thresholds(999, 800, 200, 300)
    return require(
        _refusal_reason(_thresholds=strict) == "precision",
        "a model below the precision floor was not refused for precision",
    )


def a_model_below_recall_is_refused_for_recall() -> str:
    strict = Thresholds(600, 999, 200, 300)
    return require(
        _refusal_reason(_thresholds=strict) == "recall",
        "a model below the recall floor was not refused for recall",
    )


def unexplained_disparity_is_refused_as_disparity() -> str:
    """Not as 'bias'. The residual after ground truth is the specific thing measured."""
    strict = Thresholds(600, 800, 0, 300)
    return require(
        _refusal_reason(_thresholds=strict) == "disparity",
        "a model whose flag-rate disparity exceeds what ground truth explains was not refused "
        "for disparity",
    )


def a_precision_gap_in_either_direction_is_refused() -> str:
    """The finding, as a case.

    The gate originally compared `least - most`, which is the expected shape and is *negative*
    for this model: precision is 1000/1000 in the most deprived tercile and 181/1000 in the
    least, because the labels are complete in one and full of holes in the other. A one-sided
    threshold called that a pass. See docs/BIAS-FINDING.md.
    """
    strict = Thresholds(600, 800, 200, 100)
    run, population = _run()
    report = _bias(run, population)
    return first_problem(
        require(
            report.precision_most_deprived > report.precision_least_deprived,
            "the fixture no longer reproduces the finding this case exists for; if the label "
            "coverage has been fixed, update docs/BIAS-FINDING.md rather than this assertion",
        ),
        require(
            _refusal_reason(_thresholds=strict) == "precision_gap",
            "a precision gap running the unexpected way was not refused",
        ),
    )


def a_missing_model_card_is_refused() -> str:
    return require(
        _refusal_reason(model_card=None) == "model_card_missing",
        "a model with no card was promoted. AI Act Art. 11 and Annex IV.",
    )


def a_card_from_a_different_run_is_refused() -> str:
    """Worse than a missing card: documentation that is confidently wrong."""
    run, _ = _run()
    stale = dict(_card(run))
    stale["artefact_digest"] = "0" * 32
    return require(
        _refusal_reason(model_card=stale) == "model_card_stale",
        "a card describing a different artefact was accepted",
    )


def nothing_approves_itself() -> str:
    """Doctrine 5. The same rule is an IAM deny in the estate, so both refuse the same thing."""
    return require(
        _refusal_reason(approval=Approval("pipeline", AT)) == "self_approval",
        "the pipeline approved its own model",
    )


def the_parity_door_has_no_key() -> str:
    """Doctrine 7. The one refusal no approver can waive, and attempting it is itself refused.

    A break-glass that opens everything is a rubber stamp with extra ceremony. Having exactly
    one unopenable door is what keeps the other waivers honest.
    """
    waived = _approval(waivers={"parity": "the divergence is only in the last bit"})
    return first_problem(
        require(
            _refusal_reason(parity_holds=False) == "parity",
            "a parity failure did not refuse the promotion",
        ),
        require(
            _refusal_reason(approval=waived) == "parity_waiver_attempted",
            "an approver waived the parity door. It has no key: a divergence means the number "
            "that trained the model and the number in production are different things, so "
            "nobody — including the approver — knows what they would be approving.",
        ),
        # Waived against the unbiased run, so that the waiver is the only thing standing
        # between this model and promotion. Against the shipped run the precision gap refuses
        # it first, and the case would then pass or fail for a reason unrelated to waivers.
        require(
            _refusal_reason(
                on_ground_truth=True,
                approval=_approval(waivers={"precision": "known regression, ticket 41"}),
            )
            == "promoted",
            "a waivable threshold could not be waived, which makes every door unopenable and "
            "moves the override outside the system, where it leaves no evidence",
        ),
    )


CASES: tuple[Case, ...] = (
    Case(
        "training_is_reproducible",
        "The same pinned snapshot yields the same metrics and the same artefact digest. A "
        "metric that moves is one no threshold can be set against.",
        training_is_reproducible,
    ),
    Case(
        "the_shipped_model_is_refused_for_the_finding",
        "The model trained on inspector confirmations does not pass. That is the result. "
        "Tuning the threshold until it did would have been the easy move and the dishonest one.",
        the_shipped_model_is_refused_for_the_finding,
    ),
    Case(
        "a_model_trained_on_unbiased_labels_is_promoted",
        "The calibration case, and the other half of the finding: same model, same thresholds, "
        "labels from ground truth instead of the dispatch log. It promotes.",
        a_model_trained_on_unbiased_labels_is_promoted,
    ),
    Case(
        "a_model_below_precision_is_refused_for_precision",
        "Refused *for that threshold*. A model rejected for a missing card when the real "
        "problem was recall looks identical from the outside, and somebody fixes the card.",
        a_model_below_precision_is_refused_for_precision,
    ),
    Case(
        "a_model_below_recall_is_refused_for_recall",
        "As above, for the other performance floor.",
        a_model_below_recall_is_refused_for_recall,
    ),
    Case(
        "unexplained_disparity_is_refused_as_disparity",
        "The residual after ground truth, not a generic fairness number. It is the part the "
        "world does not explain, which is the signature of the loop.",
        unexplained_disparity_is_refused_as_disparity,
    ),
    Case(
        "a_precision_gap_in_either_direction_is_refused",
        "The finding. Precision far higher where the model flags most means the labels are "
        "complete there and full of holes elsewhere — and a one-sided threshold called it a "
        "pass. See docs/BIAS-FINDING.md.",
        a_precision_gap_in_either_direction_is_refused,
    ),
    Case(
        "a_missing_model_card_is_refused",
        "AI Act Art. 11 and Annex IV. A model with no card is one nobody can state the "
        "intended purpose of after its author has left.",
        a_missing_model_card_is_refused,
    ),
    Case(
        "a_card_from_a_different_run_is_refused",
        "Worse than a missing card: documentation that is confidently wrong.",
        a_card_from_a_different_run_is_refused,
    ),
    Case(
        "nothing_approves_itself",
        "Doctrine 5, mirrored by an IAM deny on sagemaker:UpdateModelPackage in the estate so "
        "the offline gate and the deployed one refuse the same thing.",
        nothing_approves_itself,
    ),
    Case(
        "the_parity_door_has_no_key",
        "Doctrine 7. Exactly one unopenable door is what keeps the other waivers honest; a "
        "break-glass that opens everything is a rubber stamp with extra ceremony.",
        the_parity_door_has_no_key,
    ),
)
