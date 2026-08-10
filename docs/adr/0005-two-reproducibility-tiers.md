# ADR-0005 — Two reproducibility tiers, declared per model

**Status:** accepted · **Date:** 2026-08-10 · **Serves:** claim 5

## Context

Every model in this repository was fitted in closed form over integers, and two runs over one
snapshot produced a byte-identical artefact. `evals/promotion` asserts that digest.

That is the strongest reproducibility guarantee available, and it was **cheap only because the
models were small**. A one-dimensional least-squares line cannot follow a load curve with a
daily shape and a weekly one, and an operator would want the accuracy that is being left on the
table. Gradient boosting gets it.

Gradient boosting cannot make the byte-identical promise. Not through carelessness — through
arithmetic:

- **Float summation order.** Gradients are added in whatever order the data arrives in a block,
  and addition on floats is not associative. A different order is a different sum in the last
  bits.
- **Threading.** Above one thread that order is not deterministic between two runs *on the same
  machine*.
- **The library and the machine.** A different BLAS, SIMD width or version moves the last bits.

The first two are controllable. The third is not.

## Decision

**A model declares which of two tiers it is in, and the tier is verified rather than asserted.**

| Tier | Promise | Who is in it |
|---|---|---|
| `STRICT` | the same snapshot yields a **byte-identical artefact** | the closed-form models in `train.py`, and every fallback rule |
| `PRACTICAL` | the same snapshot, image digest and seed yield **identical metrics** | the boosted forecaster in `gradient.py` |

`reproducibility.verify` takes two runs and a tier and returns *which field diverged*, not a
boolean — a digest that differs while metrics agree is a serialisation problem, and metrics that
differ are a training problem, and one word should not describe both.

The pins that make `PRACTICAL` keep its word are `seed`, `nthread=1` and `tree_method="exact"`,
plus the container image digest recorded by the pipeline execution. `nthread=1` is the one
people forget: without it the model still looks reproducible right up until it is trained on a
bigger machine.

## Why not simply replace the deterministic models

Because the strict tier is load-bearing and the trade runs the wrong way. Its coefficients are
two integers, so the model can be read, a rollback restores it exactly, and it is what the
artefact-digest assertion compares. Deleting it would spend the strongest reproducibility
statement in the repository to gain accuracy on a forecast **whose fallback does not use a model
at all** (ADR-0001). The boosted model is added beside it, not instead of it.

## Why not claim STRICT for the boosted model anyway

It would pass, locally, on one machine, until it did not — intermittently, in CI, on a runner
with a different SIMD width. A guarantee that quietly depends on the machine is worse than a
weaker one stated plainly, because the weaker one tells a reviewer what they actually have.

## Consequences

- The model card records the tier. A reviewer reading "reproducible" knows which promise.
- `xgboost` is the **`ml` extra**, never a hard dependency: `make test`, every claim gate and
  every eval keep running on a laptop with the standard library and two small packages.
- `tests/test_dependencies.py::test_optional_dependencies_are_lazy` enforces that no optional
  dependency is imported at module scope. One top-level `import xgboost` in `src/watermark/` and
  the entire offline suite stops running on a machine without the extra — with nothing to say so
  but an ImportError on somebody else's laptop.
- `tests/test_gradient_tier.py` **fails rather than skips** when `WATERMARK_REQUIRE_ML` is set,
  which CI sets. A tier that silently does not run is a promise nobody is checking.
- `scripts/check_model_pins_agree.py` compares the pins in `gradient.py` against the
  `HyperParameters` in `infra/ml/pipeline.tf`. Two places holding the same experiment is two
  places that drift.
