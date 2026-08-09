"""Fitting, from the offline store, as of a pinned instant.

**As of a pinned instant** is the whole discipline. A training run that reads "the current
table" is a run nobody can repeat: the lakehouse moves, a three-day-late batch restates a
total, and the metrics from last month cannot be reproduced to argue with. Every run here
records the snapshot it read and the digest of the rows it read, so "the same snapshot yields
the same metrics" is checkable rather than hoped for.

The models are small on purpose — see the package docstring. What matters for claims 5 and 3 is
that they are fitted from the *offline store*, deterministically, and that the features they
were fitted on are the same objects the serving path produces.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from watermark.core.time import Instant


@dataclass(frozen=True, slots=True)
class Example:
    """One training row: the features as they were, and the label."""

    entity_id: str
    at: Instant
    features: tuple[int, ...]
    label: int


@dataclass(frozen=True, slots=True)
class Model:
    """A fitted model. Integer-friendly and fully described by its coefficients."""

    kind: str
    #: Scaled by 1e6 and held as integers, so that a model artefact serialises byte-identically
    #: and two runs over one snapshot produce the same bytes rather than the same number.
    coefficients: tuple[int, ...]
    intercept: int
    #: For the anomaly scorer: the score above which a meter is queued. Fitted, not chosen.
    threshold: int = 0

    _SCALE = 1_000_000

    def score(self, features: Sequence[int]) -> int:
        """The model's output, in the same scaled-integer space as its coefficients."""
        total = self.intercept
        for coefficient, value in zip(self.coefficients, features, strict=True):
            total += coefficient * value
        return total

    def digest(self) -> str:
        """A hash of the artefact. What a model card names and a rollback restores."""
        material = f"{self.kind}|{self.coefficients}|{self.intercept}|{self.threshold}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class TrainingRun:
    """A model and everything needed to reproduce or to argue with it.

    The snapshot and the data digest are not metadata. They are what makes a metric a claim
    rather than an anecdote: the same snapshot must yield the same metrics, and without the
    digest nobody can tell a model that got better from a model that read different rows.
    """

    model: Model
    #: The Iceberg snapshot the offline store was read at. Pinned, tagged, and protected from
    #: the expiry job — see ADR-0002.
    snapshot: str
    as_of: Instant
    examples: int
    data_digest: str
    metrics: dict[str, int]

    def model_card(self, intended_purpose: str, hazard: str) -> dict[str, object]:
        """AI Act Art. 11 and Annex IV in the shape a promotion gate can check.

        Generated from the run rather than written beside it. A card somebody types is a card
        that describes the model they meant to train.
        """
        return {
            "kind": self.model.kind,
            "artefact_digest": self.model.digest(),
            "trained_as_of": self.as_of.to_iso(),
            "snapshot": self.snapshot,
            "examples": self.examples,
            "data_digest": self.data_digest,
            "metrics": dict(sorted(self.metrics.items())),
            "intended_purpose": intended_purpose,
            "hazard": hazard,
        }


def _digest_of(examples: Sequence[Example]) -> str:
    """A hash of the training set, in content order.

    Sorted, so that the digest describes the *rows* and not the order a query happened to
    return them in. Two runs over one snapshot that disagreed on this would be two runs over
    different data with the same name.
    """
    material = "\n".join(
        f"{example.entity_id}|{example.at.to_iso()}|{example.features}|{example.label}"
        for example in sorted(examples, key=lambda item: (item.at.epoch_millis, item.entity_id))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def train_load_forecast(examples: Sequence[Example], snapshot: str, as_of: Instant) -> TrainingRun:
    """Least squares, in closed form, over integer features.

    One-dimensional on purpose: the substation's own recent load predicts its next interval
    better than anything else available, and a richer model would be a richer thing to explain
    without being a better thing to rely on. The fallback rule does not use it at all
    (ADR-0001), which bounds how wrong it is allowed to be.
    """
    if not examples:
        raise ValueError("no examples: a model fitted on nothing scores perfectly on nothing")

    count = len(examples)
    xs = [example.features[0] for example in examples]
    ys = [example.label for example in examples]

    mean_x = sum(xs) // count
    mean_y = sum(ys) // count
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    variance = sum((x - mean_x) ** 2 for x in xs)

    slope = (covariance * Model._SCALE) // variance if variance else 0
    intercept = mean_y * Model._SCALE - slope * mean_x

    model = Model("load_forecast", (slope,), intercept)
    residuals = [abs(model.score([x]) // Model._SCALE - y) for x, y in zip(xs, ys, strict=True)]
    return TrainingRun(
        model=model,
        snapshot=snapshot,
        as_of=as_of,
        examples=count,
        data_digest=_digest_of(examples),
        metrics={
            "mean_absolute_error_w": sum(residuals) // count,
            "max_absolute_error_w": max(residuals),
        },
    )


def train_anomaly_scorer(examples: Sequence[Example], snapshot: str, as_of: Instant) -> TrainingRun:
    """A threshold scorer, with the threshold fitted rather than chosen.

    Fitted by scanning every candidate and taking the one with the best F1. Chosen thresholds
    are how a model quietly acquires the recall its author wanted; a fitted one is at least a
    number the data produced, and the promotion gate then argues with it.
    """
    if not examples:
        raise ValueError("no examples: a scorer fitted on nothing flags nothing, perfectly")

    scores = [example.features[0] for example in examples]
    labels = [example.label for example in examples]

    best_threshold, best_f1 = min(scores), -1
    for candidate in sorted(set(scores)):
        flagged = [score >= candidate for score in scores]
        true_positives = sum(1 for f, label in zip(flagged, labels, strict=True) if f and label)
        false_positives = sum(
            1 for f, label in zip(flagged, labels, strict=True) if f and not label
        )
        false_negatives = sum(
            1 for f, label in zip(flagged, labels, strict=True) if not f and label
        )
        if not true_positives:
            continue
        precision = true_positives * 1000 // (true_positives + false_positives)
        recall = true_positives * 1000 // (true_positives + false_negatives)
        f1 = 2 * precision * recall // max(1, precision + recall)
        if f1 > best_f1:
            best_threshold, best_f1 = candidate, f1

    model = Model("anomaly_scorer", (Model._SCALE,), 0, threshold=best_threshold)
    flagged = [score >= best_threshold for score in scores]
    true_positives = sum(1 for f, label in zip(flagged, labels, strict=True) if f and label)
    false_positives = sum(1 for f, label in zip(flagged, labels, strict=True) if f and not label)
    false_negatives = sum(1 for f, label in zip(flagged, labels, strict=True) if not f and label)

    return TrainingRun(
        model=model,
        snapshot=snapshot,
        as_of=as_of,
        examples=len(examples),
        data_digest=_digest_of(examples),
        metrics={
            # Per mille throughout. Integers, so a metric in a model card is the same number on
            # every machine and a threshold comparison is exact.
            "precision_per_mille": true_positives
            * 1000
            // max(1, true_positives + false_positives),
            "recall_per_mille": true_positives * 1000 // max(1, true_positives + false_negatives),
            "f1_per_mille": best_f1,
            "flag_rate_per_mille": sum(flagged) * 1000 // len(examples),
        },
    )
