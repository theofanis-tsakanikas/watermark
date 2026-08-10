"""The practical tier keeps its promise, and does not silently stop being checked.

Two runs of the boosted forecaster over one snapshot must agree on their metrics. That is the
whole of `Tier.PRACTICAL`, and it is the number the promotion gate argues with — a model whose
mean absolute error moves between two identical runs has no error a threshold can be set
against.

**This file fails rather than skips when xgboost is absent in CI.** A skipped test reports
green from a machine that had no way of running it, which is the same shape as a gate that has
quietly stopped checking. Locally, without the `ml` extra, it skips and says so; in CI the extra
is installed and `WATERMARK_REQUIRE_ML` makes the absence an error.
"""

from __future__ import annotations

import os

import pytest

from watermark.core.time import Instant
from watermark.models.gradient import (
    HYPERPARAMETERS,
    TIER,
    XGBoostUnavailable,
    train_load_forecast_boosted,
)
from watermark.models.reproducibility import Tier, verify
from watermark.models.train import Example, train_load_forecast

SNAPSHOT = "snapshot-2026-08-10"
AT = Instant.from_iso("2026-08-10T00:00:00Z")


def _examples() -> list[Example]:
    """A load curve with a daily shape, deterministic and with no random source at all.

    The residue is what makes the boosted model earn its place: a straight line cannot follow a
    curve, so the two tiers have different errors and the comparison below is about something.
    """
    curve = [
        40,
        38,
        36,
        35,
        37,
        45,
        62,
        78,
        85,
        88,
        90,
        92,
        91,
        89,
        87,
        88,
        94,
        99,
        97,
        88,
        75,
        62,
        52,
        45,
    ]
    return [
        Example(
            f"SUB-{(index % 4) + 1:02d}",
            AT,
            (curve[index % 24] * 100,),
            curve[(index + 1) % 24] * 100,
        )
        for index in range(240)
    ]


def _boosted():
    try:
        return train_load_forecast_boosted(_examples(), SNAPSHOT, AT)
    except XGBoostUnavailable:
        if os.environ.get("WATERMARK_REQUIRE_ML"):
            pytest.fail(
                "xgboost is missing where the practical tier is required to run. This tier "
                "must not skip in CI: a promise nobody checks is not a promise."
            )
        pytest.skip("xgboost not installed; install the `ml` extra to exercise the practical tier")


def test_the_practical_tier_repeats_its_metrics() -> None:
    first, second = _boosted(), _boosted()
    divergence = verify(first, second, Tier.PRACTICAL)
    assert divergence is None, str(divergence)


def test_the_practical_tier_does_not_claim_the_strict_one() -> None:
    """The guarantee is stated where it can be read, not inferred from the word "reproducible"."""
    assert TIER is Tier.PRACTICAL
    assert "metrics" in Tier.PRACTICAL.promise
    assert "byte-identical" in Tier.STRICT.promise


def test_the_deterministic_model_is_in_the_strict_tier() -> None:
    """And it really is byte-identical, not merely declared to be."""
    first = train_load_forecast(_examples(), SNAPSHOT, AT)
    second = train_load_forecast(_examples(), SNAPSHOT, AT)
    assert verify(first, second, Tier.STRICT) is None
    assert first.model.digest() == second.model.digest()


def test_the_pins_that_make_the_promise_are_all_present() -> None:
    """Each of these has a failure mode the module docstring names.

    Asserted here rather than trusted, because removing one is a one-character edit that makes
    the model marginally faster and quietly breaks the tier — and nothing else would notice
    until two runs disagreed in CI, intermittently.
    """
    assert HYPERPARAMETERS["nthread"] == 1
    assert HYPERPARAMETERS["tree_method"] == "exact"
    assert isinstance(HYPERPARAMETERS["seed"], int)


def test_both_tiers_agree_on_the_data_they_read() -> None:
    """A boosted run and a deterministic run over one snapshot read the same rows.

    If this ever failed, every accuracy comparison between the two would be a comparison of two
    different datasets wearing one snapshot id.
    """
    boosted = _boosted()
    linear = train_load_forecast(_examples(), SNAPSHOT, AT)
    assert boosted.data_digest == linear.data_digest
    assert boosted.snapshot == linear.snapshot
