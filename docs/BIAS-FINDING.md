# The bias finding

**Measured 2026-08-09**, over the 600-meter labelled population in `data/labels.py`, with the
threshold scorer fitted by `src/watermark/models/train.py`. Reproduce with `make claim-5`.

`docs/SCENARIO.md` asked for the proxy-discrimination risk to be *"the thing actually
measured — not a generic fairness metric chosen because it is easy to compute"*, and for the
result to be written down *"including if it is uncomfortable"*. It is uncomfortable, and it is
not uncomfortable in the way that was expected.

## The numbers

| | most deprived tercile | least deprived tercile |
|---|---|---|
| meters | 180 | 180 |
| genuinely tampering | 66 | 23 |
| **confirmed** by an inspector | 66 | 4 |
| flagged by the model | 361 / 1000 | 122 / 1000 |
| **precision against confirmations** | **1000 / 1000** | **181 / 1000** |

Model metrics overall: precision 651/1000, recall 986/1000, flag rate 181/1000.

## What was expected

That the model would flag the most deprived tercile more often *and be right less often* — the
familiar shape, where a group is over-policed and the extra attention is mostly wasted. That
would have shown up as precision lower in the left-hand column.

## What was found

The opposite, and it is worse.

The model is **perfectly precise in the most deprived tercile and almost useless in the least**
— 1000/1000 against 181/1000. Not because it understands deprived areas better. Because
**every true case there was confirmed and almost none elsewhere was**: 66 of 66 against 4 of 23.
Inspectors went where they had always gone, so the labels are complete in one tercile and full
of holes in the other.

Measured against those labels the model looks excellent exactly where the dispatch log is
densest. A reviewer reading a per-group precision table would see 1000/1000 and conclude the
model is *fairest* on the group it flags three times as often.

The disparity in flagging is 2959/1000 — nearly three to one. Ground truth accounts for almost
all of it (2880/1000), which is the honest part: tampering genuinely is more common in older
installations. The residual, **78/1000, is the part the world does not explain**, and it is
small. If the analysis had stopped at demographic parity it would have reported a large
disparity and a large justification and moved on. The precision figure is what says the labels
themselves are the problem.

## What changed because of it

**The precision-gap threshold became two-sided.** It was written as
`precision_least - precision_most > ceiling`, which is the expected shape. Under the finding
that expression is *negative*, so the gate passed a model whose label coverage differs by a
factor of five between groups. It now compares the absolute difference and names the direction
in the refusal.

`BiasReport.is_uncomfortable` was widened the same way, for the same reason.

## What has not been fixed

The loop itself. A model trained on confirmations will keep learning the dispatch log, and no
threshold in the promotion gate changes that — a gate can refuse a model, it cannot supply the
labels nobody collected. The honest mitigations are outside this repository's scope and are
recorded rather than implied:

- **Randomised inspection.** A fraction of visits allocated independently of the model, which
  buys unbiased labels at the price of some wasted visits. It is the only thing on this list
  that actually breaks the loop.
- **Modelling the label process**, not just the outcome — estimating the probability a true
  case *would have been confirmed* and reweighting. Standard, and it depends on assumptions
  nobody can check with the data they have.
- **Reporting recall by tercile against ground truth**, which is possible here only because
  the data is synthetic. In production the true rate is exactly the unknown, which is what
  makes this failure invisible in the first place.

## Legal basis for the measurement

Art. 4a(2) of the AI Act, inserted by Reg. (EU) 2026/1744, permits processing special-category
personal data for bias detection *"especially where data outputs influence inputs for future
operations"* — a description of this loop. Its final sentence creates **no obligation** to do
it, and the anomaly path is not high-risk under Annex III (`docs/REGULATORY.md`, verified
2026-08-09). Measuring it is therefore a choice, and it is recorded as one.
