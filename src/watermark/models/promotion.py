"""The promotion gate. Claim 5.

No model reaches an endpoint without passing performance thresholds, bias thresholds, a model
card generated from the training run, and a named human approver — and when it is refused, it
is refused **for the stated reason**, not with a boolean.

Two doctrine rules land here as structure rather than as process.

**Doctrine 5 — nothing approves itself.** The approver is a required field with no default, and
a set of principals that may not approve is checked explicitly: the pipeline role, the training
role, and anything whose name marks it as a service. In the estate the same rule is an IAM deny
on `sagemaker:UpdateModelPackage` for those roles, so the offline gate and the deployed one
refuse the same thing for the same reason.

**Doctrine 7 — one door has no key.** Every threshold here can be waived by an approver with a
recorded reason, except one. A train/serve parity failure cannot be overridden by anybody: it
means the number that trained the model and the number in production are different things, so
nobody — including the approver — knows what they would be approving. Having exactly one
unopenable door is what keeps the other refusals honest; a break-glass that opens everything is
a rubber stamp with extra ceremony.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from watermark.core.time import Instant
from watermark.models.bias import BiasReport
from watermark.models.train import TrainingRun


class Verdict(Enum):
    PROMOTED = "promoted"
    REFUSED = "refused"


class PromotionRefused(Exception):
    """A refusal, carrying the reason. Never a bare False.

    A gate that returns a boolean teaches its caller to print "failed" and stop, and the reason
    is the only part anybody can act on.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Approval:
    """A named human's sign-off. No default for the name.

    A default of `"pipeline"` — the obvious convenience for an integration test — is how a model
    promotes itself with a plausible audit trail, and it is the one change that would make
    claim 5 false while every test kept passing.
    """

    approver: str
    at: Instant
    #: Thresholds this approver is waiving, each with a reason. `parity` is never accepted here
    #: and the gate refuses the approval itself if it appears.
    waivers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.approver.strip():
            raise ValueError("an approval needs a named human; an unnamed approver is not one")
        if any(not reason.strip() for reason in self.waivers.values()):
            raise ValueError(
                "a waiver needs a reason. A threshold set aside with no reason is a threshold "
                "that was never a threshold."
            )


@dataclass(frozen=True, slots=True)
class Thresholds:
    """What a model must clear. Declared, so a refusal can name the number it missed."""

    min_precision_per_mille: int
    min_recall_per_mille: int
    #: How much of the flag-rate disparity may remain unexplained by ground truth. Not zero:
    #: with a finite sample, zero would refuse every model including a fair one, and a gate
    #: that refuses everything is a gate somebody removes.
    max_unexplained_disparity_per_mille: int
    #: How far precision may differ between the terciles, **in either direction**.
    #:
    #: The direction was the finding. The obvious worry is a model that is *less* precise where
    #: it flags most — wasted visits concentrated on the people it already over-flags. Running
    #: the analysis produced the opposite and worse result: precision 1000/1000 in the most
    #: deprived tercile against 181/1000 in the least, because the labels there are complete
    #: and elsewhere they are not. The model looks excellent exactly where the dispatch log is
    #: densest, and a one-sided threshold would have called that a pass.
    max_precision_gap_per_mille: int


#: Principals that may never approve. Doctrine 5, as a list the offline gate can check and the
#: estate mirrors in an IAM deny on `sagemaker:UpdateModelPackage`.
#: The thresholds in force. **Policy, not a fixture.**
#:
#: They lived in `evals/promotion/` until a promotion had to be run for real, which made the
#: problem visible: the numbers a model must clear were readable only by the harness that tests
#: the gate. Anything outside the harness — a promotion script, a reviewer, an auditor asking
#: what the bar is — had nowhere to look, and a second copy would have been a second policy.
#:
#: Deliberately not the numbers the current model passes. A gate whose thresholds were chosen
#: after seeing the metrics is a gate that has never refused anything.
THRESHOLDS: Final = Thresholds(
    min_precision_per_mille=600,
    min_recall_per_mille=800,
    max_unexplained_disparity_per_mille=200,
    max_precision_gap_per_mille=300,
)

FORBIDDEN_APPROVERS = frozenset({"pipeline", "system", "watermark-pipeline", "watermark-training"})

#: The one threshold no approval can waive.
UNWAIVABLE = "parity"


@dataclass(frozen=True, slots=True)
class PromotionGate:
    """Decides whether a training run may become an endpoint."""

    thresholds: Thresholds

    def evaluate(
        self,
        run: TrainingRun,
        bias: BiasReport,
        approval: Approval,
        model_card: dict[str, object] | None,
        parity_holds: bool,
    ) -> Verdict:
        """Promote, or raise with the reason it was refused.

        The order is deliberate: parity first, because it is the one thing no approval can
        rescue, and refusing it after a long list of passing checks would read as a technicality
        rather than as the reason nothing else matters.
        """
        self._parity(parity_holds, approval)
        self._approver(approval)
        self._card(model_card, run)
        self._performance(run, approval)
        self._bias(bias, approval)
        return Verdict.PROMOTED

    def _parity(self, holds: bool, approval: Approval) -> None:
        if UNWAIVABLE in approval.waivers:
            raise PromotionRefused(
                "parity_waiver_attempted",
                "a train/serve parity failure cannot be waived, by anybody, for any reason. It "
                "means the number that trained the model and the number in production are "
                "different things, so nobody — including the approver — knows what they would "
                "be approving. This is the one door with no key (doctrine 7).",
            )
        if not holds:
            raise PromotionRefused(
                "parity",
                "train/serve parity does not hold for this model's features. Fix the "
                "divergence; there is no override.",
            )

    def _approver(self, approval: Approval) -> None:
        if approval.approver.lower() in FORBIDDEN_APPROVERS:
            raise PromotionRefused(
                "self_approval",
                f"'{approval.approver}' may not approve a promotion. No model, no pipeline and "
                "no service principal approves itself (doctrine 5); in the estate the same rule "
                "is an IAM deny on sagemaker:UpdateModelPackage for these roles.",
            )

    def _card(self, card: dict[str, object] | None, run: TrainingRun) -> None:
        if card is None:
            raise PromotionRefused(
                "model_card_missing",
                "AI Act Art. 11 and Annex IV. A model with no card is a model nobody can say "
                "the intended purpose of after the person who trained it has left.",
            )
        if card.get("artefact_digest") != run.model.digest():
            raise PromotionRefused(
                "model_card_stale",
                f"the card describes artefact {card.get('artefact_digest')} and the run "
                f"produced {run.model.digest()}. A card generated from a different run is "
                "worse than none: it is documentation that is confidently wrong.",
            )

    def _performance(self, run: TrainingRun, approval: Approval) -> None:
        precision = run.metrics.get("precision_per_mille", 0)
        recall = run.metrics.get("recall_per_mille", 0)

        if (
            precision < self.thresholds.min_precision_per_mille
            and "precision" not in approval.waivers
        ):
            raise PromotionRefused(
                "precision",
                f"{precision}/1000 against a floor of "
                f"{self.thresholds.min_precision_per_mille}/1000. Every wasted inspection is a "
                "visit to somebody's home on the strength of a wrong number.",
            )
        if recall < self.thresholds.min_recall_per_mille and "recall" not in approval.waivers:
            raise PromotionRefused(
                "recall",
                f"{recall}/1000 against a floor of {self.thresholds.min_recall_per_mille}/1000.",
            )

    def _bias(self, bias: BiasReport, approval: Approval) -> None:
        unexplained = bias.unexplained_disparity_per_mille
        if (
            unexplained > self.thresholds.max_unexplained_disparity_per_mille
            and "disparity" not in approval.waivers
        ):
            raise PromotionRefused(
                "disparity",
                f"{unexplained}/1000 of the flag-rate disparity is not explained by ground "
                f"truth, against a ceiling of "
                f"{self.thresholds.max_unexplained_disparity_per_mille}/1000. That residual is "
                "the signature of the feedback loop rather than of the world: the model is "
                "learning where inspectors went.",
            )

        gap = abs(bias.precision_least_deprived - bias.precision_most_deprived)
        if (
            gap > self.thresholds.max_precision_gap_per_mille
            and "precision_gap" not in approval.waivers
        ):
            worse_where = (
                "the most deprived tercile"
                if bias.precision_most_deprived < bias.precision_least_deprived
                else "the least deprived tercile"
            )
            raise PromotionRefused(
                "precision_gap",
                f"precision differs by {gap}/1000 between the terciles, worse in "
                f"{worse_where}, against a ceiling of "
                f"{self.thresholds.max_precision_gap_per_mille}/1000. Both directions are "
                "refused, and the second is the one actually found: precision far *higher* "
                "where the model flags most means the labels are complete there and full of "
                "holes elsewhere, so the model looks excellent exactly where the dispatch log "
                "is densest. A one-sided threshold called that a pass. See "
                "docs/BIAS-FINDING.md.",
            )


def refusals_for(gate: PromotionGate, cases: Sequence[tuple[str, object]]) -> list[str]:
    """Helper for the harness: the reason each case was refused for, or "promoted"."""
    reasons = []
    for _, arguments in cases:
        try:
            gate.evaluate(*arguments)  # type: ignore[misc]
            reasons.append("promoted")
        except PromotionRefused as refusal:
            reasons.append(refusal.reason)
    return reasons
