# ADR-0006 — Clarify runs, and does not vote

**Status:** amended 2026-08-10 — *Clarify cannot run in this account* · **Serves:** claim 5

> **Amendment.** The first pipeline execution against a real account answered:
> *"SageMaker Clarify processing is in maintenance mode and is not available to new customers."*
> The image cannot be pulled here, so the step is removed from `infra/ml/pipeline.tf`.
>
> The decision below still holds and its reasoning is unchanged — Clarify was never going to
> vote, and the measurement that showed why is unaffected. What is lost is the rendered AWS
> report; what remains is `watermark.models.clarify`, which computes the same post-training
> metrics offline in integers from the same subjects, and `evals/promotion`, which proves the
> finding in CI on every commit.
>
> That is a smaller claim, and it is the true one: **this project does not run Clarify.** It
> reimplements the three metrics that mattered and shows what they cannot see.

## Context

`src/watermark/models/bias.py` measures the risk `docs/SCENARIO.md` actually names: an
inspection model trained on a dispatch log learns where inspectors have been. It found the
finding in `docs/BIAS-FINDING.md` — precision **1000/1000** in the most deprived tercile against
**181/1000** in the least, because labels there are complete and elsewhere they are not — and
the promotion gate refuses the shipped model over it.

SageMaker Clarify was absent. `CLAUDE.md` listed it among the services this project uses, which
was untrue, and the honest reading of that gap was that a reviewer expects the industry-standard
report and this project did not produce one.

The question was not *whether* to add it. It was whether Clarify should be **a gate**.

## What was measured

The prediction, before running it, was that Clarify would pass the model this project refuses —
a neat story about a standard metric missing a subtle problem. **That prediction was wrong.**

Run over both models this repository trains, from the same subjects, with no access to ground
truth in either case:

| | Clarify disparate impact | our precision gap |
|---|---|---|
| fitted on inspector confirmations (gate **refuses**) | 727/1000 | 819/1000 |
| fitted on ground truth (gate **promotes**) | 731/1000 | 42/1000 |

Clarify refuses **both**, and removing the defect moves its number by **4 per mille** while the
defect itself moves by **777**.

## Decision

**Clarify runs on every pipeline execution and its output is recorded as model metrics. It has
no vote in the promotion gate.**

Two reasons, and each alone would be sufficient.

**It is not measuring the defect.** Clarify's post-training metrics compare outcome rates
between groups, taking the labels as given. Label incompleteness cannot move an outcome-rate
metric, because the metric never asks whether the labels are complete. A 777-point improvement
registering as 4 is not a threshold that needs tuning; it is the wrong instrument.

**Gating on it would refuse the corrected model.** The fixed model still flags one group about
three times as often, because tampering really is more common there — `bias.py` computes how
much of the disparity ground truth explains, and most of it is explained. Clarify cannot know
that, because Clarify has no ground truth and neither does production. Wiring its conventional
bounds into the gate produces a gate that refuses everything, and doctrine has a name for what
happens next: a gate that refuses everything is a gate somebody removes.

## Why not drop it, then

Because "we did not run the standard tool" is not an answer to a notified body, an auditor or a
reviewer, and because the report is genuinely wanted — in the form they expect it, attached to
the model package, as `ModelMetrics.Bias`. AI Act Art. 10(2)(f) requires examination for bias;
it does not name a metric. Producing the recognised artefact and *also* the analysis that found
the problem is strictly more than producing either.

The failure mode this avoids is the common one: adopting a standard fairness metric, watching it
pass, and concluding the model is fair. Here it passes nothing and still says nothing useful,
which is a sharper illustration of the same point.

## Consequences

- `evals/promotion` asserts **both** halves — that Clarify is insensitive to the defect, and
  that it would block the corrected model. If either stops being true, CI goes red and this ADR
  is what needs rewriting, not the assertion.
- The comparison is computed offline, in integers, from the same subjects. It is provable on a
  laptop with no AWS account, which is the difference between a claim and a screenshot.
- The pipeline's `Clarify` step produces the artefact; the `Examine` step produces the finding.
  A pipeline where only the first ran would register a model with a bias report and no analysis,
  so `Register` depends on `Examine`.

## What this does not say

It does not say Clarify is a bad tool, and it does not generalise. On a model whose defect *is*
disparate treatment with reliable labels, Clarify is the right instrument and this analysis
would be the redundant one. The claim here is narrow and measured: **on this defect, in this
dataset, it does not move.**
