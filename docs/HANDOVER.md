# Handover — 2026-08-14

**The capture passed end to end. Every job green, every claim asserted against a live estate.**

Written for whoever runs the sequence next. It supersedes the 2026-08-13 handover.

---

## What a green run looks like

Run `31796754919`, six jobs, all successful. These are its own printed numbers, not a summary:

| what the estate did | figure |
|---|---|
| rows merged into the lakehouse, this run | **279** |
| rows published with a watermark earlier than their interval end | **0** |
| distinct lineage ids | **279 for 279 rows** |
| restatements, and restatements naming what they replaced | **131 of 131** |
| claim 2 — the same day, delivered twice | **3,768 values identical**, at an offset of −900s the harness measured |
| claim 3 — two mechanisms, one contract, no tolerance | **20 agreed, 0 diverged, 0 missing** |
| curtailment — `SUB-01` peak against its limit | **485,442 W of 450,000 W → 8 throttled** |
| curtailment — the other three substations | **0 throttled**, all under their limits |
| decisions written, by origin | **500: 8 fallback, 492 withheld** |
| consequential decisions awaiting a human | **20 pending, 0 actuated** |
| the live case matrix | **7 of 7**, over 10,233 evidence lines |
| the gold layer | **25 of 25 dbt models and tests** |
| erasure | **certificate written**; `C00007-NEW`'s 54 rows gone, `C00007`'s 90 untouched |

---

## The sequence, and why it has five steps

A fresh account cannot serve a model nobody has approved, so the endpoint cannot exist on the
first deploy. That is claim 5 as a property of the *order of operations* rather than as a check,
and it is worth recording for the same reason:

```bash
# 1 — deploy, with no endpoint. There is no approved model yet.
gh workflow run deploy.yml -f layer=all -f stream_position=LATEST \
  -f online_store=true -f promoted_model="" \
  -f expires_at=<RFC-3339, a few hours out> -f confirm=apply

# 2 — capture. Trains a candidate and registers it as PendingManualApproval.
gh workflow run capture.yml -f minutes=6 -f snapshot=<RFC-3339> \
  -f threshold=700 -f labels=randomised_inspection -f approver="" -f confirm=capture

# 3 — promote. The gate approves it; nothing approves itself.
gh workflow run promote.yml -f model_package_version=1 -f approver=<a named human> ...

# 4 — deploy again, now with something to serve.
gh workflow run deploy.yml ... -f promoted_model=v1 ...

# 5 — capture again. Model decisions, the oversight queue, the endpoint.
gh workflow run capture.yml ...

# and the same day, always:
gh workflow run destroy.yml -f confirm=destroy -f layer=all
```

**Steps 3 to 5 have not been run in this order end to end.** Step 2's capture is green and
registers the model; the promotion and the endpoint-serving capture were exercised on earlier
days, before the case matrices existed. That is the first thing to do tomorrow.

---

## What the capture is now, and why

Six jobs rather than twenty-six steps, because a failure at step thirteen used to mean re-running
the twelve above it — which happened six times in one day at about fifty minutes each.

```
drive ─┐
       ├─ aggregate ─┐
train ─┼─ decide ────┼─ stop  (always)
       └─ erase ─────┘
```

`drive` and `train` are independent and run together. `erase` waits for `aggregate` and `decide`
**because it is destructive**: the three ran in parallel once and the erasure deleted rows from
`silver.meter_interval` while the parity comparison was reading them, producing
`claim 3: 19 agreed, 1 diverged` on a system where nothing was wrong.

`stop` needs every job and runs `if: always()`. As a step that was one condition; as a job it has
to be declared, or a failure in the middle leaves Managed Flink billing.

---

## Where every declared case is checked

Two matrices, and the second is what makes the first mean anything about the deployed system.

| | question | where |
|---|---|---|
| `evals/cases/` — 11 cases | does the **core** see every defect the cast declares? | CI, on every push |
| `scripts/cases_live.py` — 7 cases | does the **estate** see them? | the `decide` job |

Both carry a guard that fails when a cohort is declared and unchecked, or declared and empty —
because a fixed list of tests cannot catch an omission: the list looks complete by being the list.
Two of the cast's defects were exercised by nothing at all until an audit found them.

The seven claim harnesses add 53 more offline cases. `make preflight` runs 29 checks.

---

## What is left

**Run the five-step sequence in order.** Steps 3 to 5 are the gap: a promotion and then a capture
with an endpoint, so that model decisions and the oversight queue's *positive* half are exercised
alongside the case matrices.

**Claim 7's second half.** The refusal is proved on every run: 20 pending, 0 actuated, and
actuating raises before any review exists. That a *named human* is the only thing that can
actuate needs a name on a record, and putting one there is the owner's decision.
`capture.yml` takes `approver` for it.

**The savepoint-restore drill** (`tests_flink/`) is skipped. It needs a way to hold a MiniCluster
job open, cancel it with a savepoint and resume — a harness, not an assertion.

**`settlement_publication`** is a third decision in `contracts/decisions/`, horizon four days,
`actuation: advisory`. The settlement path now produces numbers, but that contract is not
exercised by any harness. Worth an audit like the one that found the tariff gap.

**Model Monitor and Clarify** are closed to new AWS accounts (`docs/AWS-CONSTRAINTS.md`).

**Both environment reviewers** (`deploy` and `destroy`) are still removed. They came off so the
session could iterate without approval on every run. **Restore them.**

---

## Things that cost a run today, worth not repeating

**Do not push while a deploy is running.** `ci.yml` has `cancel-in-progress: true` on the ref, so
a push cancels the `verify` job the deploy depends on and the deploy dies as `cancelled`.

**A capture is longer than the role's one-hour session.** Each job now assumes the role itself,
so this is handled — but if a job grows past an hour, add a renewal rather than raising
`max_session_duration` in the bootstrap layer.

**`always()` guarantees a step runs, not that it works.** The cleanup ran with expired credentials
and Managed Flink billed for two hours. Credentials are renewed immediately before it now, and it
*reads* the application state rather than asserting it.

---

## Cost

Nothing is standing. Yesterday's tagged spend was USD 16.44; today's is not yet reported — a cost
allocation tag takes up to 24 hours to activate and Cost Explorer lags a day, so a zero on the
day of a run means "not yet counted", never "free".
