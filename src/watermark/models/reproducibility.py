"""What "reproducible" is allowed to mean, per model, declared rather than assumed.

Every model in this repository carries a **tier**, and the tier is a promise about what two
runs will agree on. There are two, they are not interchangeable, and the reason there are two
is worth the paragraph.

--------------------------------------------------------------------------------------------
STRICT — the same snapshot yields the same *bytes*
--------------------------------------------------------------------------------------------

The deterministic models in `train.py` fit in closed form over integers. Two runs over one
snapshot produce artefacts with the same digest, on any machine, in any order, on any Python
that implements the arithmetic correctly. Nothing is sampled, nothing is threaded, nothing sums
floats.

This is the strongest guarantee available and it is *cheap here only because the models are
small*. It is what lets `evals/promotion` assert an artefact digest rather than a metric within
a band, and it is why the fallback rules — the ones that must be computable when everything
else has failed — live in this tier.

--------------------------------------------------------------------------------------------
PRACTICAL — the same snapshot, image and seed yield the same *metrics*
--------------------------------------------------------------------------------------------

Gradient boosting cannot make the strict promise, and pretending otherwise is the mistake this
module exists to prevent. Three things break byte-identity, none of them a bug:

- **Float summation order.** Histogram construction adds gradients in whatever order the data
  arrives in a block. Addition on floats is not associative, so a different order is a
  different sum in the last bits.
- **Threading.** With more than one thread the order above is not even deterministic between
  two runs on the same machine.
- **The library and the machine.** A different BLAS, a different SIMD width, a different
  version — each moves the last bits.

The first two are controllable: pin the seed, pin `nthread=1`, pin `tree_method` to an exact
method rather than an approximate one. The third is not, and a guarantee that quietly depends
on the machine is worse than a weaker guarantee stated plainly. So the practical tier promises
what it can keep: **pinned snapshot + pinned image digest + pinned seed ⇒ the same metrics**,
where "the same" means identical after the integer rounding the gate compares on.

--------------------------------------------------------------------------------------------
Why this is a module and not a comment
--------------------------------------------------------------------------------------------

Because a tier that is written down and never checked is a tier that decays into the stronger
one everybody assumes. `verify` below takes two runs and the tier they claim, and says whether
the claim holds. `evals/promotion` runs it, and the model card records the tier — so a reviewer
reading a card knows which of the two promises they are being given, rather than inferring the
strong one from the word "reproducible".

**A model may not be promoted into a tier it has not been verified in.** That is not enforced
by convention: `Tier.verify` is what the harness calls, and it fails on a model whose runs
disagree in the way its tier forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Tier(Enum):
    """The two promises. Ordered: STRICT implies PRACTICAL, never the reverse."""

    STRICT = "strict"
    PRACTICAL = "practical"

    @property
    def promise(self) -> str:
        if self is Tier.STRICT:
            return "the same snapshot yields a byte-identical artefact"
        return "the same snapshot, image digest and seed yield identical metrics"


@dataclass(frozen=True, slots=True)
class Divergence:
    """What differed between two runs that were supposed to agree."""

    tier: Tier
    field: str
    first: object
    second: object

    def __str__(self) -> str:
        return (
            f"{self.tier.value} tier promises {self.tier.promise}, and {self.field} differs: "
            f"{self.first!r} against {self.second!r}"
        )


def verify(first, second, tier: Tier) -> Divergence | None:
    """Check two runs of the same training against the tier they claim.

    Returns the divergence, or `None` when the promise holds. A boolean would have been shorter
    and would have made every failure read the same; the interesting part of a broken promise is
    *which* field moved, because that names the cause. A digest that differs while the metrics
    agree is a serialisation problem; metrics that differ are a training problem.

    Both tiers check the inputs first. Two runs that read different rows are not two runs of the
    same training, and comparing their outputs would answer a question nobody asked.
    """
    if first.snapshot != second.snapshot:
        return Divergence(tier, "snapshot", first.snapshot, second.snapshot)
    if first.data_digest != second.data_digest:
        return Divergence(tier, "data_digest", first.data_digest, second.data_digest)

    # Metrics are the floor. A practical-tier model that cannot repeat its own metrics has no
    # number a threshold can be set against, and a strict-tier one is already broken.
    if first.metrics != second.metrics:
        return Divergence(tier, "metrics", first.metrics, second.metrics)

    if tier is Tier.STRICT and first.model.digest() != second.model.digest():
        return Divergence(tier, "artefact digest", first.model.digest(), second.model.digest())

    return None
