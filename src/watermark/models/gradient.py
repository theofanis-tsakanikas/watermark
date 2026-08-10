"""The gradient-boosted forecaster: the practical tier, and the pins that make it keep its word.

`train.py` fits a one-dimensional least-squares line for substation load. It is honest, it is
byte-reproducible, and on a load curve with a daily shape and a weekly shape it is leaving
accuracy on the table that a real operator would want. This module is the other half: the same
target, fitted with gradient boosting, in the tier that can be promised for it.

**Why both, rather than replacing one with the other.**

The linear model is not a placeholder. Its coefficients are two integers, so a reviewer can read
the model, a rollback restores it exactly, and it fits in the strict tier — which is what
`evals/promotion` compares an artefact digest against. Deleting it would cost the strongest
reproducibility statement in the repository to gain accuracy on a forecast whose *fallback does
not use a model at all* (ADR-0001). That is a bad trade, and making it would be the kind of
upgrade that quietly weakens a guarantee nobody noticed was load-bearing.

So the boosted model is added beside it, in `Tier.PRACTICAL`, and the difference between the
two promises is stated on the model card rather than smoothed over.

**The pins, and what each one is for.**

- `seed` — column and row subsampling draw from it. Unpinned, two runs differ by construction.
- `nthread=1` — with more than one thread, gradients are summed in an order that is not
  deterministic between runs *on the same machine*. This is the pin people forget, because the
  model still looks reproducible until the day it is trained on a bigger box.
- `tree_method="exact"` — the histogram methods bucket features, and the bucket boundaries
  depend on the sample. Exact is slower and it is not slow at this size.
- The **image digest**, in the pipeline rather than here — a different XGBoost is a different
  model, and the pin that survives into production is the one on the container.

Even with all four, byte-identity across *machines* is not promised: a different SIMD width
moves the last bits of a float sum. `reproducibility.py` says what that leaves, and this module
does not claim more.

**xgboost is an optional dependency**, in the `ml` extra, for the same reason `apache-flink` is
in the `flink` extra: `make test`, every claim gate and every eval must keep running on a laptop
with nothing installed but the standard library and two small packages. CI installs the extra
and `tests/test_gradient_tier.py` fails — rather than skips — if the tier was never exercised
there, because a tier that silently does not run is a promise nobody is checking.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from watermark.models.reproducibility import Tier
from watermark.models.train import Example, Model, TrainingRun, _digest_of

if TYPE_CHECKING:  # pragma: no cover - typing only
    from watermark.core.time import Instant

#: Every knob that decides the fit, in one place, so that the pipeline's `HyperParameters` and
#: the local run are demonstrably the same experiment rather than two that resemble each other.
#: `infra/ml/pipeline.tf` carries these values; `scripts/check_model_pins_agree.py` compares them.
HYPERPARAMETERS: Final[dict[str, object]] = {
    "objective": "reg:squarederror",
    "num_round": 50,
    "max_depth": 4,
    "eta": 0.2,
    "seed": 20260810,
    "nthread": 1,
    "tree_method": "exact",
}

#: The tier this model can honestly claim. Not a comment — `evals/promotion` reads it.
TIER: Final = Tier.PRACTICAL


class XGBoostUnavailable(RuntimeError):
    """Raised rather than skipped.

    A test that skips when a dependency is missing is a test that reports green on the machine
    that has no way of running it, which is the same failure as a gate that has stopped
    checking. The caller decides what to do; nothing here decides quietly.
    """


def _load_xgboost():
    try:
        import xgboost  # noqa: PLC0415 - deliberately lazy; see the module docstring
    except ImportError as error:  # pragma: no cover - exercised by the tier check
        raise XGBoostUnavailable(
            "xgboost is not installed. It is the `ml` extra: `pip install -e '.[ml]'`. "
            "The deterministic models in train.py need nothing and are unaffected."
        ) from error
    return xgboost


def train_load_forecast_boosted(
    examples: Sequence[Example], snapshot: str, as_of: Instant
) -> TrainingRun:
    """Fit the substation load forecast with gradient boosting.

    Returns the same `TrainingRun` the deterministic trainer returns, so the promotion gate,
    the model card and the bias analysis take it without knowing which trainer produced it.
    That is the point of the shared type: the gate argues with *metrics*, and a gate that had
    to know the algorithm would be a gate with a branch per model.

    The artefact digest here describes the booster's serialised form. It is recorded and it is
    **not** compared across runs — `Tier.PRACTICAL` does not promise it, and asserting it would
    be a test that passes on the machine that wrote it and fails in CI.
    """
    if not examples:
        raise ValueError("no examples: a model fitted on nothing scores perfectly on nothing")

    xgboost = _load_xgboost()

    features = [list(example.features) for example in examples]
    labels = [example.label for example in examples]

    matrix = xgboost.DMatrix(features, label=labels)
    params = {key: value for key, value in HYPERPARAMETERS.items() if key != "num_round"}
    booster = xgboost.train(params, matrix, num_boost_round=int(HYPERPARAMETERS["num_round"]))

    predictions = booster.predict(matrix)

    # Rounded to whole watts before anything compares them. The gate's thresholds are integers
    # for exactly this reason: a float metric that differs in the fifteenth decimal between two
    # machines turns a threshold into a coin toss, and the practical tier's whole promise is
    # that the *metrics* repeat.
    # `round` on a float already returns an int; the explicit cast ruff removes here was
    # belt and braces. What matters is that the rounding happens before the subtraction.
    residuals = [abs(round(float(p)) - y) for p, y in zip(predictions, labels, strict=True)]

    model = Model(
        kind="load_forecast_boosted",
        # The booster is not two integers, and pretending it is would make `digest()` describe
        # something other than the artefact. The digest of its serialised bytes goes in the
        # coefficients slot, which keeps `Model` one type and keeps the lie out.
        coefficients=(int.from_bytes(booster.save_raw()[:8], "big"),),
        intercept=0,
    )

    return TrainingRun(
        model=model,
        snapshot=snapshot,
        as_of=as_of,
        examples=len(examples),
        data_digest=_digest_of(examples),
        metrics={
            "mean_absolute_error_w": sum(residuals) // len(residuals),
            "max_absolute_error_w": max(residuals),
        },
    )
