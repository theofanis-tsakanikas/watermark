# Handover — 2026-08-17

**The five-step sequence ran in order, and every job of both captures was green.** Claim 7's
second half has a name on a record for the first time, and claim 6's six legs were each confirmed
against the estate rather than against the certificate that claims them.

Supersedes the 2026-08-16 handover.

---

## What the sequence proved

```
deploy (no endpoint) → capture → promote → deploy (endpoint) → capture
```

**Run `32007561614` — the capture with no endpoint.** Six jobs, all green.

| what the estate did | figure |
|---|---|
| the day, anchored once | **07:53:41Z**, shared by the seed and the publisher |
| claim 2 — the same day, delivered twice | **3,779 values identical** |
| the Glue Data Quality ruleset | **6 of 6 rules, score 1.00** |
| claim 3 — all three features | agreed, **0 diverged**, across `meter_consumption_1h`, `substation_load_15m`, `substation_headroom_15m` |
| curtailment — `SUB-01` | **485,442 W of 450,000 W → 8 throttled**; the other three, 0 |
| the live case matrix | **7 of 7** |
| **the erasure** | **all 6 legs confirmed independently of the certificate** |

**Run `32016454038` — the promotion.** The gate evaluated version 5 and approved it. The registry
records it in as many words:

> Approved by Theofanis Tsakanikas via promote.yml run 32016454038. Gate: scripts/promote.py,
> thresholds in watermark.models.promotion.

`randomised_inspection` is what the gate promotes; `dispatch_log` is what it refuses, for the
finding in `docs/BIAS-FINDING.md`. Same population, same model class, same thresholds — the
difference is the labels, and that is the finding.

**Run `32017770602` — the capture with the endpoint.** Six jobs, all green, and two things that
had never been exercised live:

```
the endpoint answers, and the answer is recorded   ✓   (capture objects: 1)
oversight queue: 20 pending, 0 actuated
actuated 190ddaaad590f753a6eb47b266aef377 on the review of Theofanis Tsakanikas
```

The refusal was proved on every previous run. What was not proved is the other half: that a
**named human** is the only thing that can actuate. Without a name, the queue shows only that
nothing gets through; with one, it shows exactly what it takes for something to.

---

## What today closed

| what it was | how it showed |
|---|---|
| **The `offline_store` leg did not exist.** `ErasureScope` declared six legs, the machine produced five, and `EveryLegConfirmed` was a five-way AND over `$.legs[0..4]` — a condition that cannot notice a missing leg, because the missing leg is what changes the count | Four of a subject's feature rows survived an erasure that certified. The branch exists now, bounded by the assignment history; the refusal counts against `local.erasure_legs`; and `check_erasure_legs.py` holds the scope, the machine's branches and that list equal |
| **The offline store is eventually consistent.** A record already in flight landed forty-six seconds after the DELETE — `is_deleted false`, a real feature value | The leg waits the documented flush window. Correct here only because nothing writes the subject once the decision layer has finished; **in production the control is a write-block before the erasure, and this platform has none** |
| **The seed and the publisher shifted the day apart.** One read the clock in `drive`, the other inside `train` after the training pipeline | Half an hour of that puts `M00019`'s 14:30 tariff change back on the hour, where no settled hour straddles it. One anchor, read once where the day starts, handed to both |
| **My first version of that anchor was the dispatch time** | `drive` reaches the publisher half an hour later, so the day ended before the capture began and the two deliveries stopped being the same shape. Claim 2 reported a thousand disagreements with the signature of a one-interval shift — my own change, found by the harness it broke |
| **An erased meter read as a parity failure.** `DeleteRecord` is a soft delete and the tombstone's event time outlives any re-materialisation | Claim 3 was reporting an erasure that worked as a divergence. The meter is named and excluded — nothing here asks the erasure to be undone |
| **Ninety minutes to test a ten-minute fix** | `capture.yml` takes `-f from_stage` now, as `deploy.yml` has taken `-f layer=` since it existed. `decide` is deliberately not a resume point: its matrix is bounded by what the landing prefix held *before* the capture, and a run that did not drive the day cannot reconstruct that |

---

## Still open, and all of it deliberate

Four items, each in `contracts/waivers.yaml` with a name and a date. `scripts/check_waivers.py`
turns CI red on expiry with no commit behind it.

| | |
|---|---|
| The savepoint-restore drill | WV-001 — a harness, not an assertion |
| The `deploy` and `destroy` environment reviewers | WV-003 — **restore them** |
| Model Monitor and Clarify | WV-002 — closed by AWS to this account class |
| `gold.settlement_hour` has no ruleset | it names a table dbt builds, which does not exist during a deploy |

---

## Worth not repeating

**A thirty-minute capture costs about ninety minutes.** `minutes` is paid twice — the drive
publishes the compressed day and claim 2 re-drives the whole of it. Use `-f from_stage=erase` to
test the erasure; it is ten minutes.

**Use GitHub's own re-run-failed-jobs for transient failures.** Maven rate-limiting the runners,
a compaction losing a commit race — those need the same commit run again, not a new one.

**A state-machine or IAM change needs its layer applied before the capture that exercises it.**

**`gh run watch` can exit early**, reporting a run finished when it has not. Poll the run's own
`status`.

---

## Cost

**The estate is standing, with an endpoint.** `watermark-anomaly` is `InService` and a real-time
endpoint is one of the three expensive things CLAUDE.md says are never left standing. Its
`watermark:expires-at` is 2026-08-17T15:42Z and the reaper — which genuinely deletes since
2026-08-15 — sweeps hourly. It was left up deliberately, at the owner's instruction.
