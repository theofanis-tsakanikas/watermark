# Handover — 2026-08-16

**Five jobs of six green, and the sixth found the thing this whole apparatus was built to find:
claim 6's refusal is proved offline against a module the deployed system does not use.**

Supersedes the 2026-08-15 handover.

---

## What went green today

Both of yesterday's blockers closed, and the second one closed by revealing a disagreement
neither check could see on its own.

| | |
|---|---|
| `stand up and drive the day` | green — claim 1 in SQL, claim 2, the quality ruleset |
| `train a candidate and register who was in it` | green |
| `build the gold layer` | green — 25 of 25 dbt models and tests |
| `serve, compare and decide` | **green, 7 of 7 live cases** — first time |
| `an erasure certifies` | **five of six legs confirmed** — see below |
| `stop everything that bills` | green |

**The evidence gap.** Yesterday's guess was right, even though it would not reproduce offline: the
live case matrix was reading two shifted copies of the same day, because claim 2 re-drives the
whole thing. Bounded to the first delivery's 58 part files, the declared gap is visible again.

**The quiet substation.** Closing the first blocker exposed a second one that had been hidden by
it. Once both checks were scoped to the same delivery, they disagreed — and not about the data:
`capture.yml` *reports* a missing `held_back` with its reasoning written out, and `cases_live.py`
*failed the run* for it. The disagreement had been invisible because the workflow step was reading
the whole landing prefix and always found a hold-back from some earlier capture.

The arithmetic settles which is right. SUB-03's silence is forty minutes of event time and the
publisher compresses arrivals nearly 300×, so the gap passes in about eight seconds of wall clock;
whether a one-second batch boundary lands inside it has gone either way in three captures out of
six. The property is asserted where it is deterministic — seven cases in `evals/watermark/`, and
claim 1 in SQL on every run as a statement about output rather than timing. Three tests now pin
that decision so it is not re-tightened by somebody who has not read the arithmetic.

---

## The finding

`ErasureScope.legs` declares six legs. The state machine produces five, and they do not even
carry the same names:

| the scope declares | the state machine produces |
|---|---|
| `crypto_shred` | `crypto_shred` |
| `lakehouse_rows` | `physical_deletion` |
| **`offline_store`** | **— nothing —** |
| `online_store` | `online_store` |
| `training_sets` | `training_sets` |
| `model_artefacts` | `model_artefacts` |

**`offline_store` has no branch, no step and no line in the certificate.** The independent check
found four of the subject's rows still in the SageMaker offline store after an erasure that
certified. Claim 6's own sentence names that store: *"removes the subject from the lakehouse, the
offline store, the online store and every training set."*

And the reason it could go unnoticed is the deeper half:

```
"EveryLegConfirmed": {
  "Type": "Choice",
  "Choices": [{ "And": [
    { "Variable": "$.legs[0].confirmed", "BooleanEquals": true },
    ... through $.legs[4] ...
```

**Five hard-coded positional indices.** `src/watermark/erasure/certificate.py` refuses to issue
unless every leg the *scope* declares has confirmed, and `evals/erasure/` proves that in nine
cases — but `issue()` is never called in the estate. The deployed certificate is written by an S3
`putObject` after a five-way AND over array positions. The offline store is not missing from a
list somebody forgot to extend; it is missing from a list that does not exist.

That is the shape this repository spends most of its effort refusing, arriving in its own claim 6:
a property proved offline against a mechanism production does not use.

**Do not close this by adding a sixth index.** The fix is that the estate's refusal is derived
from the scope rather than hand-counted — and `gate-proof` needs a mutation that removes a leg and
requires the refusal, or the next one will be as quiet as this one.

The offline store was designed for this. `infra/ml/feature_store.tf` sets `table_format = "Iceberg"`
with the comment: *"so the offline store is a table the erasure path can issue row-level deletes
against. A Glue-format offline store is append-only files, and claim 6 would then have one leg it
could only satisfy by rewriting the prefix."* The design anticipated the leg. Nobody wrote it.

---

## Fixed today

| what it was | how it showed |
|---|---|
| **The online-store leg deleted a customer id from a store keyed by meter.** `DeleteRecord` with `$.subject_id` against `watermark-meter-consumption`, whose identifier is `meter_id`. Deleting an identifier that does not exist returns cleanly, so the leg reported `confirmed: true` having done nothing — on every erasure this estate has ever run | The first independent check found the record still there. The meters are now resolved from the assignment history, and only the ones the subject holds *now* |
| **My own `lakehouse_rows` check demanded over-deletion.** It counted every row the meter ever produced and reported 54 survivors; `M00007` changes customer at 10:00, so those belong to the predecessor and must survive | The certificate step beside it had this right all along, through the SCD-2 join. Same lesson as the replay counts: the assertion and the thing it asserts about must agree on the question |
| **My `offline_store` check named a table that has never existed.** SageMaker names the offline store's Glue table itself | `DescribeFeatureGroup` is the only thing that knows what it called it, and asking it is also what keeps the check independent |
| **A failing dbt test said "Got 1 result" and stopped.** The rows sit behind Lake Formation, so nobody outside the job can look | The compiled SQL is already in `target/`; failing tests are re-run and their rows printed |
| **Maven Central rate-limits GitHub's runners.** A deploy died on `curl 403` for a URL that served 200 from a laptop a minute later | Five attempts with a widening wait. The jar stays unvendored for the reason written above the target |

---

## Still open

| task | what would close it |
|---|---|
| **The offline-store leg** | An Iceberg `DELETE` against the feature group's offline table, bounded by the assignment history the way `DeleteRowsPhysically` is |
| **The estate's refusal is hand-counted** | `EveryLegConfirmed` derived from `ErasureScope.legs`, plus a `gate-proof` mutation that drops a leg and requires the refusal |
| **`lakehouse_rows` and `physical_deletion` are one leg under two names** | One name, and something that fails when the scope and the machine disagree about the set |
| **The five-step sequence, in order** | `deploy → capture → promote → deploy → capture`, green throughout. **The promotion will be approved, not refused** — `randomised_inspection` is the mitigation the gate promotes; `dispatch_log` is the one it refuses |
| **Claim 7's second half** | A capture with `approver` set, and the actuated decision carrying the name |

Four more are open on purpose with dates in `contracts/waivers.yaml`: the savepoint-restore drill,
the environment reviewers, Model Monitor and Clarify, and `gold.settlement_hour`'s ruleset.

---

## Worth not repeating

**A thirty-minute capture costs about ninety minutes.** `minutes` is paid twice — the drive
publishes the compressed day and claim 2 re-drives the whole of it.

**A change to the state machine needs `deploy.yml -f layer=governance` before the capture that
exercises it.** Two runs were spent learning the equivalent for IAM.

**`gh run watch` can exit early.** It reported a capture finished when it had not; poll the run's
own `status` field rather than trusting the watcher's exit.

---

## Cost

Nothing is standing; destroyed through `destroy.yml` and verified from the CLI.
