"""Measuring the risk `docs/SCENARIO.md` actually names, not a metric that is easy to compute.

The risk is specific and it is not "the model treats groups differently". It is a **feedback
loop**:

> a model trained on *confirmed* cases learns where inspectors historically went, not where
> tampering historically was.

Old installations, irregular consumption and prepayment correlate with lower-income areas.
Inspectors were sent there. The confirmations came from there. A model trained on them sends
inspectors there, and the next round of labels confirms it. Nothing in the accuracy figure
moves; the model gets better at predicting its own history.

So what is measured here is **the correlation between the flag rate and a deprivation proxy the
model was never given** — and, separately, whether that correlation is *stronger in the labels
than in the ground truth*, which is the signature of the loop rather than of the world.

A demographic-parity number alone would not distinguish the two. If deprivation genuinely
correlates with tampering, a model that finds it is right; if it correlates only with where
inspectors went, the model is learning the dispatch log. The second comparison is what tells
them apart, and it is the reason this file is longer than a fairness metric usually is.

**On the legal basis.** Art. 4a(2) of the AI Act, inserted by Reg. (EU) 2026/1744, permits
processing special-category data for exactly this — "especially where data outputs influence
inputs for future operations", which is a description of this loop. Its last sentence creates
no obligation to do it. Doing it anyway is a choice, and `docs/REGULATORY.md` says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Where the terciles cut. Terciles rather than deciles because with a fleet this size a
#: per-decile rate is three meters and a rounding error, and a metric computed on three meters
#: moves when one of them changes. The honest granularity is the one the data supports.
MOST_DEPRIVED_AT_OR_BELOW = 2
LEAST_DEPRIVED_AT_OR_ABOVE = 7


@dataclass(frozen=True, slots=True)
class Subject:
    """One entity, its outcome, and the proxy the model was never shown."""

    entity_id: str
    #: 0 to 9. Never a model input — if it were, the measurement would be of a feature rather
    #: than of a leak.
    deprivation_decile: int
    flagged: bool
    #: What the inspector *confirmed*. This is the label the model trains on, and it is the
    #: dispatch log as much as it is the world.
    confirmed: bool
    #: What was actually true, known here because the data is synthetic and unknowable in
    #: production. Its absence in reality is the whole reason the loop is hard to see.
    truly_tampering: bool


@dataclass(frozen=True, slots=True)
class BiasReport:
    """What was found. Written down whether or not it is comfortable."""

    #: Flag rate per mille in the most deprived tercile and in the least.
    flag_rate_most_deprived: int
    flag_rate_least_deprived: int
    #: The ratio, per mille. 1000 is parity; 2000 means the most deprived tercile is flagged
    #: twice as often.
    disparity_per_mille: int

    #: The same ratio computed on *ground truth* rather than on the model. If tampering really
    #: is more common in one tercile, a model that finds it is right, and this is the number
    #: that says so.
    true_disparity_per_mille: int

    #: The signature of the loop: how much of the model's disparity is *not* explained by the
    #: truth. Positive means the model is more skewed than the world.
    unexplained_disparity_per_mille: int

    #: Precision in each tercile. A model that is *less* precise where it flags most is
    #: sending inspectors on more wasted visits to the people it already over-flags.
    precision_most_deprived: int
    precision_least_deprived: int

    subjects: int

    @property
    def is_uncomfortable(self) -> bool:
        """Whether this report says something nobody wanted it to say.

        Not a pass/fail. The promotion gate decides that against declared thresholds; this
        exists so a human reading the report cannot skim past the finding.
        """
        return self.unexplained_disparity_per_mille > 0 or (
            # Either direction. The first draft only looked for precision being *worse* where
            # the model flags most; running it found the reverse, which is worse — see
            # docs/BIAS-FINDING.md.
            self.precision_most_deprived != self.precision_least_deprived
        )

    def summary(self) -> str:
        return (
            f"{self.subjects} subjects. Flag rate {self.flag_rate_most_deprived}/1000 in the "
            f"most deprived tercile against {self.flag_rate_least_deprived}/1000 in the least "
            f"— a disparity of {self.disparity_per_mille}/1000, of which "
            f"{self.unexplained_disparity_per_mille}/1000 is not explained by ground truth. "
            f"Precision {self.precision_most_deprived}/1000 against "
            f"{self.precision_least_deprived}/1000."
        )


def _rate(values: Sequence[bool]) -> int:
    return sum(values) * 1000 // len(values) if values else 0


def measure_proxy_discrimination(subjects: Sequence[Subject]) -> BiasReport:
    """Compare the model's behaviour across deprivation terciles, against the truth."""
    if not subjects:
        raise ValueError("no subjects: a bias report over nobody is a green tick over nobody")

    most = [
        subject for subject in subjects if subject.deprivation_decile <= MOST_DEPRIVED_AT_OR_BELOW
    ]
    least = [
        subject for subject in subjects if subject.deprivation_decile >= LEAST_DEPRIVED_AT_OR_ABOVE
    ]
    if not most or not least:
        raise ValueError(
            "one of the terciles is empty, so there is nothing to compare. A report that "
            "silently compared a group with itself would read as parity."
        )

    flag_most = _rate([subject.flagged for subject in most])
    flag_least = _rate([subject.flagged for subject in least])
    true_most = _rate([subject.truly_tampering for subject in most])
    true_least = _rate([subject.truly_tampering for subject in least])

    disparity = flag_most * 1000 // max(1, flag_least)
    true_disparity = true_most * 1000 // max(1, true_least)

    flagged_most = [subject for subject in most if subject.flagged]
    flagged_least = [subject for subject in least if subject.flagged]

    return BiasReport(
        flag_rate_most_deprived=flag_most,
        flag_rate_least_deprived=flag_least,
        disparity_per_mille=disparity,
        true_disparity_per_mille=true_disparity,
        # The residual. This is the number that distinguishes a model finding a real pattern
        # from a model reproducing a dispatch log, and it is the reason a demographic-parity
        # figure on its own would be unable to tell them apart.
        unexplained_disparity_per_mille=max(0, disparity - true_disparity),
        precision_most_deprived=_rate([subject.confirmed for subject in flagged_most]),
        precision_least_deprived=_rate([subject.confirmed for subject in flagged_least]),
        subjects=len(subjects),
    )
