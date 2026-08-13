# Handover — 2026-08-13

Written for whoever runs `deploy` tomorrow. It says what changed, what is proved, what is left,
and the exact commands to pick it up. It supersedes the 2026-08-12 handover, which described an
estate that had never taken a decision.

---

## What today was

Yesterday ended with the streaming path proved and three claims unproved live. Today closed all
three, found **nineteen defects**, and left one thing failing that is understood and small.

The pattern is the finding, not the count. **Every defect was in a component that only one path
calls, and every one had been written carefully, reviewed, and never executed.** Nine were on the
erasure path; the rest were on paths that only came into existence today. `CLAUDE.md` says
"done = runs + tested", and it held for the paths somebody runs daily.

---

## What is proved live, and where the proof is

Run `31674034190` (06:29) is the last capture that passed **every** step. Runs after it added
capability and are described below.

| claim | what the estate did |
|---|---|
| **1** — no decision from an unclosed window | 63 watermark transitions, 4 into `held_back` naming `SUB-01`; zero rows published with a watermark earlier than their interval end |
| **2** — identical replay | one lineage id per row; every restatement names what it replaced. *Live two-run comparison is the one thing still failing — see below* |
| **3** — train/serve parity | **20 agreed, 0 diverged, 0 missing**, two mechanisms over one contract, no tolerance |
| **4** — no decision on a stale feature | zero model decisions past the freshness budget; every input age in the record |
| **5** — no ungated promotion | nothing approves itself; the gate refuses `dispatch_log` and promotes `randomised_inspection` |
| **6** — erasure to a declared boundary | **certificate written**, five legs confirmed; the customer who held the same meter earlier kept all 40 of their rows; a second request from an already-erased subject certifies again |
| **7** — no automatic consequential decision | 20 pending, 0 actuated; actuation raises before any review exists |
| **core ≡ Flink** (ADR-0003 tier two) | **green, and now required** — it was `NotImplementedError` since it was written |

---

## What was built today

Seven capabilities that did not exist this morning.

**Substation telemetry** (`data/telemetry.py`, a third IoT rule). The curtailment decision —
first in `CLAUDE.md`, argued high-risk under Annex III(2) — ran against `telemetry=None` and
withheld every time. The one decision with a physical consequence had never been taken. `SUB-01`
is now driven past its limit on purpose so a capture contains a throttle and not only a day of
`release`.

**The tariff change** (`gold.settlement_priced`). `docs/SCENARIO.md` declares it, `data/cast.py`
builds the history, and **nothing read it** — the word `tariff` appeared only in `pit.py`'s
docstrings. A declared case with no consumer cannot fail, which is worse than one that fails.

**The gold layer.** `dbt build` runs in the capture. The models have existed since phase 2 and no
capture ever built them, which is why `settlement_hour` did not exist.

**Absolute silence** (`silent_after`). I had written that detecting it would cost claim 2 its
identical replay because the core would need a clock. That was wrong: `observe` is *given* the
instant it reasons at. The clock stays outside; only the subtraction is inside.

**The live replay harness**, **the MiniCluster equivalence harness**, and **the decision layer
running in the cloud** — 452 lines that nothing in AWS had ever imported.

---

## The one thing still failing, and it is small

`claim 2 — the same day, delivered twice` fails with:

```
first run: 9664 published values; replay: 4062
```

9,664 is **every capture the estate has ever driven**, not one delivery. The landing prefix
accumulates, so the copy taken before the replay contains months of history, and the offset
normalisation is computed against the oldest interval in the bucket rather than against this
run's own first.

**The fix is a third set.** Record the file list at the *top* of the capture, before the first
publish, and subtract it from both sides — exactly as `--after` already subtracts `--first`.
`scripts/replay_live.py`'s docstring carries the reasoning; the change is in `capture.yml`.

This is the same shape as a defect fixed an hour earlier in the lakehouse assertion: **a prefix
that grows across runs, read as though it were one run.** Worth looking for a third instance.

---

## The nineteen defects, and the four that are worth reading

The full list is in the commit log — every commit names what failed and why. Four are worth a
minute each.

**`always()` guarantees a step runs, not that it works.** The cleanup step has carried
`if: always()` since it was written, with a comment calling it the most important line in the
file. It ran. Every AWS call inside it returned `ExpiredTokenException`, and Managed Flink billed
for two hours until the account was checked by hand. Credentials are now renewed immediately
before the cleanup, and the step **reads** the application state rather than asserting it — the
summary used to print "The Flink application is stopped" on the strength of having *issued* a
stop.

**A restatement carried a watermark that had permitted nothing.** `closed_at` is documented as
"the watermark that permitted publication", and every result was stamped with the current
watermark — including restatements, which are deliberately allowed through while the stream is
stalled. Two rows reached the lakehouse with a `closed_at` from before their interval began. A
window closes once; a correction does not move that instant.

**`decision_id` was the contract's name.** Every decision this platform had ever taken about
every person carried the string `meter_anomaly`. The oversight queue is a mapping keyed on it, so
twenty decisions about twenty people collapsed into one entry and nineteen were dropped silently
— claim 7 failing inside the structure built to guarantee it.

**A linter asked for the code that breaks production.** `ruff`'s `UP017` wants `datetime.UTC` on
a repository targeting 3.12; Glue 4.0 runs 3.10, where the name does not exist. The job died on
import after paying for a Spark cluster. `scripts/check_glue_runtime.py` is the floor under that
now, and it says in its own docstring that it is a name list rather than a typecheck.

---

## What is left, stated rather than implied

- **The live replay**, above. First task tomorrow.
- **The savepoint-restore drill** (`tests_flink/`) is skipped. It needs a way to hold a
  MiniCluster job open, cancel it with a savepoint and resume — a harness, not an assertion.
- **Model Monitor and Clarify** are closed to new AWS accounts (`docs/AWS-CONSTRAINTS.md`).
- **Claim 7's second half.** The refusal is proved. That a *named human* is the only thing that
  can actuate needs a name on a record, and putting one there is the owner's decision, not the
  implementer's. `capture.yml` takes `approver` for it.
- **Both environment reviewers** (`deploy` and `destroy`) are still removed. They came off so the
  session could iterate without approval on every run. **Restore them.**

---

## Picking it up tomorrow

```bash
# 1. Deploy. `all` applies foundation → streaming → lakehouse → ml → governance in order.
gh workflow run deploy.yml -f layer=all -f stream_position=LATEST \
  -f online_store=true -f promoted_model=v1 \
  -f expires_at=<a few hours out, RFC-3339> -f confirm=apply

# 2. Capture. Six minutes of scenario is enough; the run itself takes about forty-five.
gh workflow run capture.yml -f minutes=6 -f snapshot=<RFC-3339> \
  -f threshold=700 -f labels=randomised_inspection -f approver="" -f confirm=capture

# 3. Destroy, the same day. Everything except bootstrap.
gh workflow run destroy.yml -f confirm=destroy -f layer=all
```

**Do not push while a deploy is running.** `ci.yml` has `cancel-in-progress: true` on the ref, so
a push cancels the `verify` job the deploy depends on and the deploy dies as `cancelled`. Three
runs were lost to this before it was understood.

**A capture is now longer than an hour** because claim 2 publishes the day twice. The deploy role's
session is capped at 3600 seconds by `infra/bootstrap/oidc.tf`, so the workflow re-assumes the
role twice. If a capture grows further, add another renewal rather than raising the cap — the
bootstrap layer applies from a laptop, and working around a workflow that can simply ask again is
not a reason to touch it.

---

## Cost

Yesterday's tagged spend was **USD 16.44**. Today's is not yet reported — a cost allocation tag
takes up to 24 hours to activate and Cost Explorer lags a day, so a zero on the day of a run
means "not yet counted", never "free".

The two hours of unstopped Flink are in today's figure and were avoidable. That is what the
cleanup fix is for.
