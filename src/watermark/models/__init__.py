"""Training, bias analysis, and the gate a model must pass to reach an endpoint.

No scikit-learn, no numpy. Not asceticism: the models here are deliberately small — a
closed-form least-squares fit and a threshold scorer — because the interesting engineering in
this project is not the model, and a dependency that pulls in a BLAS is a dependency that makes
`make test` slower than the thing it is testing.

It also keeps the training **exactly reproducible**, which claim 5 leans on: the same pinned
snapshot must yield the same metrics, and a library that sums floats in a thread-dependent
order does not give you that.

Three files, and the middle one is the one worth reading:

- `train.py` — fits from the offline store as of a pinned instant.
- `bias.py` — measures the proxy-discrimination risk `docs/SCENARIO.md` actually names, rather
  than a fairness metric chosen because it is easy to compute.
- `promotion.py` — the gate. Performance, bias, a model card, and a named approver who is not
  the pipeline.
"""

from __future__ import annotations

from watermark.models.bias import BiasReport, measure_proxy_discrimination
from watermark.models.promotion import PromotionGate, PromotionRefused, Verdict
from watermark.models.train import Model, TrainingRun, train_anomaly_scorer, train_load_forecast

__all__ = [
    "BiasReport",
    "Model",
    "PromotionGate",
    "PromotionRefused",
    "TrainingRun",
    "Verdict",
    "measure_proxy_discrimination",
    "train_anomaly_scorer",
    "train_load_forecast",
]
