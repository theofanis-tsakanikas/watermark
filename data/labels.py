"""Tampering, the confirmations, and the loop between them.

`docs/SCENARIO.md` asks for a genuine tampering signature, two lookalikes that are not
tampering, and a demographic proxy strong enough that a careless model picks it up. This is
where those are generated, and the important part is *how the labels are made*.

The truth and the labels are different things here, deliberately:

- `truly_tampering` is the world. Weakly correlated with deprivation, because in the scenario
  it genuinely is a little more common in older installations.
- `confirmed` is the **dispatch log**. Inspectors historically went to deprived areas more
  often, so a true case there was found and a true case elsewhere was missed.

A model trained on `confirmed` therefore learns where inspectors went. The gap between the two
correlations is what `models/bias.py` measures, and it exists in this fixture because it exists
in the domain — not because it makes the bias analysis produce an interesting number.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from watermark.models.bias import MOST_DEPRIVED_AT_OR_BELOW

#: Roughly how many meters in the fleet are genuinely tampering. Low, as it is in reality —
#: which is what makes precision the metric that matters rather than accuracy.
_TAMPERING_IN_PER_MILLE: Final = 120

#: How much more likely a *true* case is in the most deprived tercile. Real, and small.
_TRUE_DEPRIVATION_LIFT: Final = 3

#: How much more likely a true case was to be *confirmed* there. This is the dispatch log, and
#: it is the number the model would inherit.
_CONFIRMATION_LIFT: Final = 6


def _draw(meter_id: str, salt: str) -> int:
    """A stable pseudo-random integer 0 to 999 from the meter and a salt.

    A hash rather than `random`, for the reason the generator gives: reproducible today is not
    the same as reproducible in ten years, and a committed recording is a promise about ten
    years.
    """
    digest = hashlib.sha256(f"{salt}|{meter_id}".encode()).hexdigest()
    return int(digest[:6], 16) % 1000


@dataclass(frozen=True, slots=True)
class Label:
    """One meter's truth, and what the historical process recorded about it."""

    meter_id: str
    deprivation_decile: int
    truly_tampering: bool
    #: What an inspector confirmed. Only ever true where the truth is true — the historical
    #: process missed cases, it did not invent them.
    confirmed: bool
    #: An anomaly score the model sees, per mille. Elevated for true tampering, and elevated
    #: for two lookalikes that are not tampering at all.
    score: int


#: How many meters the bias analysis runs over.
#:
#: Larger than the forty in `data/cast.py`, and deliberately decoupled from it. The stream
#: fixture is forty meters because every watermark and replay pathology is present at that size
#: and a bigger day would cost seconds on every test run for nothing. A *bias* finding at forty
#: meters is four true cases and a rounding error — `docs/SCENARIO.md` asks for a real result
#: rather than a green tick, and a real result needs a population. Labels need no telemetry, so
#: this one costs nothing to make bigger.
POPULATION: Final = 600


def labels(population: int = POPULATION) -> tuple[Label, ...]:
    """The labelled fleet, deterministic and in meter order."""
    produced = []
    for index in range(population):
        meter_id = f"M{index:05d}"
        decile = (index * 7) % 10
        deprived = decile <= MOST_DEPRIVED_AT_OR_BELOW

        truth_draw = _draw(meter_id, "truth")
        threshold = _TAMPERING_IN_PER_MILLE * (_TRUE_DEPRIVATION_LIFT if deprived else 1)
        truly = truth_draw < threshold

        # The dispatch log. A true case in a deprived area was six times more likely to be
        # looked at, so it is six times more likely to be in the training labels.
        confirm_draw = _draw(meter_id, "confirmed")
        confirmed = truly and confirm_draw < (167 * _CONFIRMATION_LIFT if deprived else 167)

        # Two lookalikes: a meter with a solar inverter and one on a prepayment tariff. Both
        # produce irregular consumption and neither is tampering. They are in the fixture so
        # that precision is a real number rather than an artefact of a clean signal.
        lookalike = meter_id in LOOKALIKES

        base = 200 + _draw(meter_id, "score") % 300
        score = base + (500 if truly else 0) + (400 if lookalike else 0)

        produced.append(Label(meter_id, decile, truly, confirmed, min(999, score)))
    return tuple(produced)


#: The two meters that look like tampering and are not. Named, so a harness can assert that a
#: model flagged them and be explicit that flagging them is the cost of catching the real ones.
LOOKALIKES: Final = ("M00004", "M00022")
