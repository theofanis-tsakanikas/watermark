"""SageMaker Clarify: the industry-standard bias report, and where it cannot see.

Two things live here, and the second is the reason for the first.

**The analysis configuration** Clarify consumes, generated from the same subjects
`bias.py` measures, so the two analyses are demonstrably looking at one dataset rather than at
two that were prepared separately and happen to agree.

**The same metrics, computed offline.** Clarify's pre-training and post-training bias metrics
are arithmetic over counts, and reimplementing the three that matter here costs forty lines. It
buys the thing this repository exists to buy: the comparison is provable on a laptop with no
AWS account, in CI, on every commit — rather than being an assertion about what a processing
job in another account said one afternoon.

--------------------------------------------------------------------------------------------
Why run Clarify at all, when `bias.py` already found the problem — and what it measured
--------------------------------------------------------------------------------------------

The first guess was that Clarify would pass a model this project refuses. It was wrong, and the
measured answer is more useful. Run over the two models this repository trains — the one fitted
on inspector confirmations, which the gate refuses, and the one fitted on ground truth, which
the gate promotes — Clarify's disparate impact reads:

    fitted on confirmations   DI 727/1000   precision gap 819/1000
    fitted on ground truth    DI 731/1000   precision gap  42/1000

**Clarify refuses both, and its number barely moves.** The defect collapses by 777 per mille
and Clarify's metric shifts by four. It is not measuring the defect.

What it *is* measuring is the outcome disparity: meters in the most deprived tercile are
flagged about three times as often. That disparity is largely real — `bias.py` computes how
much of it ground truth explains, and most of it is explained. Clarify cannot know that,
because Clarify has no ground truth, and neither does production.

So two things follow, and both matter more than the original guess:

**Clarify is insensitive to the failure that mattered here.** Label incompleteness does not
move an outcome-rate metric, because the metric never asks whether the labels are complete.

**Clarify would block the corrected model.** The fixed model still flags one group three times
as often — because tampering really is more common there — so its DI is still 731. Wiring
Clarify's conventional bounds straight into the promotion gate would refuse a model this
project considers correct, and a gate that refuses everything is a gate somebody removes.

That is why Clarify runs and does not vote. It produces the report a reviewer, an auditor or a
notified body expects to see, in the form they expect it; `bias.py` produces the finding. Using
either alone would have been wrong in a different direction.

`docs/BIAS-FINDING.md` is the write-up. `docs/adr/0006-clarify-runs-but-does-not-vote.md` is
the decision.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from watermark.models.bias import BiasReport, Subject

#: The tercile boundaries `bias.py` uses, restated here rather than imported, because Clarify
#: needs them as *data* in a configuration file and a function cannot be serialised into JSON.
MOST_DEPRIVED_MAX: Final = 2
LEAST_DEPRIVED_MIN: Final = 7

#: Clarify reports Disparate Impact as a ratio. The convention that a value inside
#: [0.8, 1.25] is unremarkable is the US four-fifths rule, and it is used here as what it is —
#: a widely recognised reference point, not a legal test under any instrument this project
#: cites. The EU AI Act names no numeric fairness threshold; Art. 10(2)(f) requires examination
#: for bias, not a ratio.
DISPARATE_IMPACT_FLOOR_PER_MILLE: Final = 800
DISPARATE_IMPACT_CEILING_PER_MILLE: Final = 1250


@dataclass(frozen=True, slots=True)
class ClarifyReport:
    """The three post-training metrics Clarify would compute for this dataset.

    Per mille throughout, and integers, for the same reason as everywhere else in this
    repository: a float comparison that is equal on one machine and not on another turns a
    gate into a coin toss.
    """

    #: Difference in Positive Proportions in Predicted Labels. Positive means the most deprived
    #: tercile is flagged more often. Clarify calls this DPPL.
    positive_proportion_difference: int

    #: Disparate Impact — the ratio of favourable-outcome rates. Here the "favourable" outcome
    #: is *not being flagged*, because being flagged is what sends an inspector to your home.
    disparate_impact: int

    #: Recall difference between the groups, against the labels as recorded.
    recall_difference: int

    @property
    def within_conventional_bounds(self) -> bool:
        """What a reviewer reading a Clarify report would conclude.

        Deliberately named for what it is. It is not `is_fair`, and it is not `passes`: it is
        whether three numbers sit inside a range people commonly treat as unremarkable.
        """
        return (
            DISPARATE_IMPACT_FLOOR_PER_MILLE
            <= self.disparate_impact
            <= DISPARATE_IMPACT_CEILING_PER_MILLE
        )

    def summary(self) -> str:
        return (
            f"DPPL {self.positive_proportion_difference:+d}/1000, "
            f"disparate impact {self.disparate_impact}/1000, "
            f"recall difference {self.recall_difference:+d}/1000"
        )


def _per_mille(numerator: int, denominator: int) -> int:
    return (numerator * 1000) // denominator if denominator else 0


def measure_as_clarify_would(subjects: Sequence[Subject]) -> ClarifyReport:
    """Compute Clarify's post-training metrics from the recorded labels.

    Every count here comes from `subject.flagged` and `subject.confirmed` — the model's output
    and *the dispatch log*. `subject.truly_tampering` is never read, and that omission is the
    whole point: Clarify has no access to ground truth either, because in production nobody
    does. This function is blind in exactly the way the real tool is blind.
    """
    most = [s for s in subjects if s.deprivation_decile <= MOST_DEPRIVED_MAX]
    least = [s for s in subjects if s.deprivation_decile >= LEAST_DEPRIVED_MIN]
    if not most or not least:
        raise ValueError("both terciles must be populated: a comparison needs two groups")

    flagged_most = _per_mille(sum(1 for s in most if s.flagged), len(most))
    flagged_least = _per_mille(sum(1 for s in least if s.flagged), len(least))

    # Favourable = not flagged. A flag is a visit from an inspector, so the group receiving
    # fewer of them is the group receiving the favourable outcome.
    unflagged_most = 1000 - flagged_most
    unflagged_least = 1000 - flagged_least

    positives_most = [s for s in most if s.confirmed]
    positives_least = [s for s in least if s.confirmed]
    recall_most = _per_mille(sum(1 for s in positives_most if s.flagged), len(positives_most))
    recall_least = _per_mille(sum(1 for s in positives_least if s.flagged), len(positives_least))

    return ClarifyReport(
        positive_proportion_difference=flagged_most - flagged_least,
        disparate_impact=_per_mille(unflagged_most, unflagged_least),
        recall_difference=recall_most - recall_least,
    )


@dataclass(frozen=True, slots=True)
class Sensitivity:
    """How each analysis responds when the defect is removed.

    The comparison that matters is not one model's numbers. It is what happens to those numbers
    when the *known* defect is fixed — here, by fitting the same model class on ground truth
    instead of on the dispatch log. An analysis worth gating on moves. One that does not move
    is reporting something else.
    """

    #: Clarify's disparate impact, broken model and fixed model.
    clarify_broken: int
    clarify_fixed: int

    #: Our precision gap, broken model and fixed model.
    ours_broken: int
    ours_fixed: int

    @property
    def clarify_movement(self) -> int:
        return abs(self.clarify_fixed - self.clarify_broken)

    @property
    def our_movement(self) -> int:
        return abs(self.ours_fixed - self.ours_broken)

    @property
    def clarify_is_insensitive(self) -> bool:
        """Clarify barely reacts to a defect that our analysis reacts to strongly.

        An order of magnitude is the bar, and it is generous: measured, the two move by 4 and
        777 per mille respectively. If a change ever brings them within 10x, this stops being
        evidence and the harness that asserts it goes red — the same discipline as a gate-proof
        mutation whose target has moved.
        """
        return self.our_movement > self.clarify_movement * 10

    @property
    def clarify_would_block_the_fixed_model(self) -> bool:
        """The expensive half. A metric that refuses the corrected model is one nobody keeps."""
        return not (
            DISPARATE_IMPACT_FLOOR_PER_MILLE
            <= self.clarify_fixed
            <= DISPARATE_IMPACT_CEILING_PER_MILLE
        )

    def explain(self) -> str:
        return (
            "Removing the defect moves our measurement by "
            f"{self.our_movement}/1000 and Clarify's by {self.clarify_movement}/1000.\n"
            f"  Clarify : {self.clarify_broken} → {self.clarify_fixed} "
            f"({'still refuses' if self.clarify_would_block_the_fixed_model else 'now passes'} "
            "the corrected model)\n"
            f"  Ours    : precision gap {self.ours_broken} → {self.ours_fixed}\n"
            "Clarify measures the outcome disparity, which ground truth largely explains and "
            "which the correction does not remove. It never asks whether the labels are "
            "complete, so label incompleteness cannot move it. That is why it runs and does "
            "not vote."
        )


def sensitivity(
    *, broken: ClarifyReport, broken_ours: BiasReport, fixed: ClarifyReport, fixed_ours: BiasReport
) -> Sensitivity:
    """Put the two analyses' responses side by side."""
    return Sensitivity(
        clarify_broken=broken.disparate_impact,
        clarify_fixed=fixed.disparate_impact,
        ours_broken=abs(broken_ours.precision_least_deprived - broken_ours.precision_most_deprived),
        ours_fixed=abs(fixed_ours.precision_least_deprived - fixed_ours.precision_most_deprived),
    )


def analysis_configuration(
    *,
    dataset_uri: str,
    output_uri: str,
    label_column: str = "confirmed",
    prediction_column: str = "flagged",
) -> dict[str, object]:
    """The `analysis_config.json` a Clarify processing job reads.

    Generated rather than committed as a static file. The facet boundaries and the column names
    have to agree with the dataset the pipeline just wrote, and a hand-maintained copy is one
    that agrees on the day it is written — the same argument as every other published value in
    this repository.

    `deprivation_decile` appears here as a **facet**, which is Clarify's word for the attribute
    it compares across. It is never a model input: `bias.py` says the same thing in its own
    comment, and if it ever became one, this analysis would be measuring a feature rather than
    a leak.
    """
    return {
        "dataset_type": "text/csv",
        "headers": ["entity_id", "deprivation_decile", prediction_column, label_column],
        "label": label_column,
        "predicted_label": prediction_column,
        "dataset_uri": dataset_uri,
        "methods": {
            "pre_training_bias": {"methods": ["CI", "DPL"]},
            "post_training_bias": {"methods": ["DPPL", "DI", "RD"]},
            "report": {"name": "report", "title": "Watermark — meter anomaly bias analysis"},
        },
        "facet": [
            {
                "name_or_index": "deprivation_decile",
                # The most deprived tercile is the facet value; everything else is the
                # comparison group. Stated as a threshold rather than a list of deciles so the
                # configuration says what it means.
                "value_or_threshold": [MOST_DEPRIVED_MAX],
            }
        ],
        # Being flagged sends an inspector to somebody's home, so the *favourable* outcome is
        # not being flagged. Getting this backwards inverts every metric in the report while
        # producing numbers that look entirely plausible.
        "label_values_or_threshold": [0],
        "output_path": output_uri,
    }
