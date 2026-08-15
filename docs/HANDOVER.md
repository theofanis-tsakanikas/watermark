# Handover — 2026-08-15

**The longest run this project has had, and the most productive: nine real defects, every one of
them invisible offline.** The capture is not green. Two things stand between it and green, both
understood, neither large.

Supersedes the 2026-08-14 handover.

---

## Where it got to

Run `31872845346`, a thirty-minute capture. Four jobs of six green.

| what the estate did | figure |
|---|---|
| deliveries driven, compressed | **4,312 across 4 substations, 191×** (peak ~44 records/s) |
| claim 2 — the same day, delivered twice | **3,779 values identical**, at the −1800 s offset the harness measured |
| revisions in the first run | **0: 3,867 · 1: 285** — a day with corrections in it |
| **the Glue Data Quality ruleset** | **6 of 6 rules, score 1.00** — the first evaluation in the project's history |
| claim 3 — `meter_consumption_1h` | **20 agreed, 0 diverged, 0 missing** |
| claim 3 — `substation_load_15m` | **4 agreed, 0 diverged** — never once compared before today |
| claim 3 — `substation_headroom_15m` | **4 agreed, 0 diverged** — never once compared before today |
| telemetry landed | **2,304 rows** into a table that had no writer at all |
| curtailment — `SUB-01` | **485,442 W of 450,000 W → 8 throttled**; the other three, 0 |
| decisions by origin | **8 fallback, 492 withheld** |
| consequential decisions awaiting a human | **20 pending, 0 actuated** |
| the live case matrix | **6 of 7** |

---

## What is between this and green

### 1 · `the evidence gap leaves a hole` — the one failing case

> no meter the cast declares with missing intervals published fewer windows than the fleet's 97

`data/cast.py` gives the meter at `_GAP_POSITION` two intervals it never reports (`41`, `42`).
The case asserts that this shows up as *fewer published windows for that meter*. It did not: every
meter published 97.

**Do not relax the case.** Its own message is the argument — *"a gap that fills itself in is worse
than one that stays open: an hour built from three intervals is a different statement, not a
smaller total"*. Something filled the gap, and what filled it is the question.

The likely candidate is the second delivery. `cases_live.py` reads everything that landed after
`landing-before.txt`, and claim 2's step re-drives the whole day into a *running* stream — those
records land in windows the first delivery already closed and are absorbed as corrections. If the
gap meter's two intervals arrive in that second pass, the hole closes legitimately and the case is
asking its question of the wrong evidence set. **Check that before changing anything else**: it is
one comparison between the first delivery's files and the second's.

If that is the answer, the fix is scoping, not the cast: the case wants the *first* delivery.

### 2 · `an erasure certifies` has never completed

Skipped in every run today, because `decide` failed ahead of it. The six-leg verification
(`scripts/erasure_legs_live.py`) has run live exactly once, on 2026-08-15 at 05:46, and crashed
parsing the certificate — fixed since, and covered by five offline tests against the real document
shape, but **not once observed working against an estate.**

Claim 6 is therefore closed in code and unproved live. It is the first thing to watch tomorrow,
and it needs nothing but a capture that reaches it.

---

## The nine defects this run found

Every one needed a live estate. None was visible from a laptop.

| # | what it was |
|---|---|
| 1 | **The reaper deleted nothing.** It classified every expired resource, logged `would delete`, returned a list — hourly, for months. `DELETERS` named an API per type and nothing called one. |
| 2 | **The `held_back` check was green on last week's evidence.** It read the whole landing prefix, which accumulates across every capture ever driven. The scoped case matrix disagreed with it, and the matrix was right. |
| 3 | **`align` counted keys, not agreement** — while its docstring claimed the opposite. A contiguous day overlaps itself at neighbouring offsets, so a two-window edge effect chose the offset; the estate landed one interval out and reported 3,612 phantom disagreements. |
| 4 | **`substation_telemetry` was a Terraform stub Athena refuses to write to.** The one table that never learnt ADR-0008, because nothing had ever written to it. |
| 5 | **`headroom_w` was a column no table had**, named by a feature contract that loaded and validated cleanly. |
| 6 | **The doctrine-4 quality rule was wrong three ways** — `ColumnLength` on a `bigint`, `where` before the expression instead of after, and `revision > 0` conflated with *is a restatement*. It had never run, so it had never been wrong out loud. |
| 7 | **The maintenance role had no Data Quality permissions**, because nothing had ever evaluated the ruleset. Glue starts the run and reads its own progress under the same role; `Start…` without `Get…` begins work it cannot observe. |
| 8 | **Compaction and the merge raced.** `cron(30 * * * ? *)` against a merge that started at 03:30:47: `Cannot commit, missing data files`. Neither was wrong; the writer had not implemented optimistic concurrency. |
| 9 | **A Glue column comment over 255 characters** passes `terraform validate` and dies in the plan, four minutes into an apply. |

And one non-defect worth the same weight: **the budget guard was not missing.** `infra/bootstrap/cost.tf`
has had it all along — budget, notifications, deny policy, action. It was reported missing because
one layer was searched and an absence was read as a fact. The duplicate is reverted.

---

## What is still not proved, beyond the two above

**The five-step sequence, in order.** Every capture today was `deploy → capture` with
`promoted_model=""` and `approver=""`. No promotion ran, no endpoint was served.

**Claim 7's second half.** The refusal is proved on every run — 20 pending, 0 actuated — and
actuating raises before any review exists. That a *named human* is the only thing that can actuate
still has no name on a record. The owner's name goes in `capture.yml -f approver=` on the run
after the promotion.

**Expect the promotion to be refused, and that is the proof.** Claim 5's documented result is that
the gate refuses the model this repository trained, for the finding in `docs/BIAS-FINDING.md`. A
refusal is claim 5 working; it also means there is no approved model to serve, so the endpoint leg
needs a model that passes rather than a rerun of the same one.

Four more are open **on purpose, with dates**, in `contracts/waivers.yaml`: the savepoint-restore
drill, the environment reviewers, Model Monitor and Clarify, and `gold.settlement_hour`'s ruleset.
`scripts/check_waivers.py` turns CI red on expiry with no commit behind it.

---

## Things that cost time today, worth not repeating

**A thirty-minute capture costs an hour.** `minutes` is paid twice: the drive publishes the
compressed day, and claim 2 re-drives the whole of it to compare two deliveries. Six minutes was
not enough for the `held_back` case — the 40-minute silence compresses to ten seconds — and thirty
is what made it reliable.

**Do not push while a deploy is running.** `ci.yml` has `cancel-in-progress: true` on the ref.

**An IAM change needs the layer re-applied before the capture that uses it.** Two runs were spent
learning this the slow way.

**Read the whole repository before reporting something missing.** Finding a control absent from
`infra/foundation/` is not finding it absent.

---

## Cost

Nothing is standing; the estate was destroyed through `destroy.yml` and verified from the CLI. The
day held eight captures and eleven applies, which is far more than a normal day — tomorrow's
tagged figure will reflect that, and it will still under-report, because a cost allocation tag
takes up to 24 hours to activate and Cost Explorer lags a day. A zero on the day of a run means
"not yet counted", never "free".
