# PLAN — the four phases, and what closes each one

`CLAUDE.md` names this file as the definition of done and, for most of the project's life, it did
not exist. That is worth stating rather than quietly fixing: a plan reconstructed from finished
work is a description, not a plan, and the difference shows in the last section — the only one
whose items have not been done, and therefore the only one that is not a summary.

**Done here means one thing: it runs, it is tested, and something refuses it when it breaks.**
Generated-but-unrun code is not done, and a check with no `gate-proof` mutation against it is not
a check. Every task below carries the artefact that proves it.

---

## Phase 1 — the core, and the two claims that need nothing else · **done**

The stream logic as pure functions over plain data, and the two claims provable from it alone.

| task | done when | where |
|---|---|---|
| Windowing, watermarks, deduplication, lateness | `src/watermark/core/` imports no Flink, no boto3, no AWS SDK | `check_core_is_pure.py` |
| Claim 1 — no decision from an unclosed window | 7 cases, including a silent substation, a stalled stream and a device three hours fast | `evals/watermark/` |
| Claim 2 — replay is identical | 5 cases; the same day under five seeds, delivered twice, byte-identical with identical lineage | `evals/replay/` |
| The synthetic cast | Every declared defect observable, and reproducing `recordings/day.json` exactly | `make seed-check` |
| The contract layer | Three families of YAML, no Python importing a contract by name | `check_contracts.py` |

**What closed it:** claims 1 and 2 green offline, the core provably free of frameworks, and a
`gate-proof` mutation against each.

---

## Phase 2 — features, decisions, and the claim nothing else proves · **done**

| task | done when | where |
|---|---|---|
| Feature registry, offline and online resolution | Two genuinely different mechanisms sharing only the contract | `check_parity_paths_are_independent.py` |
| Claim 3 — train/serve parity | As-of SQL against a streaming materialiser, no tolerance | `evals/parity/` |
| Claim 4 — no decision on a stale feature | A feature past its budget is never served; the fallback marker survives to the record | `evals/freshness/` |
| The decision engine and its fallbacks | Silence is the safe state only where the contract says so | `evals/freshness/`, `evals/settlement/` |
| Claim 7 — no automatic consequential decision | The contract does not load; the actuation type cannot be constructed | `evals/oversight/` |

**What closed it:** claim 3 is the one nothing else in this portfolio proves, and it is proved
twice — offline against a model of the mechanisms, and live against the deployed ones.

---

## Phase 3 — the estate, and the claims that need one · **done**

| task | done when | where |
|---|---|---|
| Six Terraform layers, state isolated per layer | `terraform validate` on all six, no cross-layer state reads | `tf_validate.py` |
| OIDC, no long-lived keys | Every trusted subject names this repository and one environment | `check_oidc_subjects.py` |
| Claim 5 — the promotion gate | 12 cases, and **the model this repository trained is refused** | `evals/promotion/`, `docs/BIAS-FINDING.md` |
| Claim 6 — erasure to a declared boundary | 9 cases offline; live, every leg confirmed against the estate rather than against the certificate | `evals/erasure/`, `erasure_legs_live.py` |
| The live case matrix | The same questions asked of the deployed system, not only of the core | `cases_live.py` |
| Deploy, capture, destroy, all gated | Nothing applied outside a workflow; the estate stood up, driven and torn down | `.github/workflows/` |

**What closed it:** a full capture green end to end, and a destroy verified afterwards.

---

## Phase 4 — the things a running estate teaches · **in progress**

Everything above is finished. This is not, and the items are here because each was found by
asking the same question a different way: *what does this repository claim that nothing checks?*

### Closed in this phase

| task | what it was | what closed it |
|---|---|---|
| Attack coverage was partial | Six new checks had no mutation, and eleven under `scripts/` were never run by one | `test_every_check_script_is_run_by_a_mutation` — mechanical, so a new check ships red |
| Doctrine 6 was unimplemented | "Exceptions expire" was a sentence with nothing behind it | `contracts/waivers.yaml` + `check_waivers.py`, red on its own schedule |
| Erasure verified one leg of six | The certificate was checking itself | `erasure_legs_live.py` asks the estate through different services |
| A contract nothing read | `settlement_publication` existed before the settlement code and no harness ever named it | `evals/settlement/` + a guard over every decision contract |
| Two features that could not be served | `substation_telemetry` was a catalogue entry with no writer; `headroom_w` was a column no table had | `check_feature_sources.py`, `land_telemetry.py`, and all three features compared live |
| A ruleset never evaluated | Six Glue Data Quality rules, applied and attached, never once run against a row | `evaluate_quality.py`, reporting rule by rule |
| A reaper that deleted nothing | It classified, logged `would delete`, and returned a list — hourly, convincingly | `WATERMARK_REAPER_MODE`, ten tests over every branch that deletes |
| A budget guard that did not exist | `CLAUDE.md` described it; the account had never had one | `aws_budgets_budget` + an action that denies creation and never deletion |

### Open

| task | why it is not done | what would close it |
|---|---|---|
| **The five-step sequence, in order** | Steps 3–5 — promote, redeploy with an endpoint, capture again — have each run, but not in sequence with the case matrices present | One run of `deploy → capture → promote → deploy → capture`, green throughout |
| **Claim 7's second half** | The refusal is proved on every run: 20 pending, 0 actuated. That a *named human* is the only thing that can actuate needs a name on a record | A capture with `approver` set, and the actuated decision carrying it |
| **The budget action** | It needs `iam:CreatePolicy`, and bootstrap is the one layer that applies from a laptop | A bootstrap apply, then `budget_action_enabled = true` — WV-004 |
| **The savepoint-restore drill** | A harness rather than an assertion: hold a job open, cancel with a savepoint, resume | A test in `tests_flink/` that survives the break — WV-001 |
| **Required reviewers** | Removed so a session could iterate without an approval prompt on every one of six runs in a day | Attach one to `deploy` and `destroy` — WV-003 |
| **Model Monitor and Clarify** | Closed by AWS to accounts of this class. Not a decision this repository made and no work here reopens it | AWS reopening them, or the bias leg moving to a service that is open — WV-002 |
| **`gold.settlement_hour` has no ruleset** | It names a table dbt builds, which does not exist at any point during a deploy | The gold layer built inside the capture rather than by hand |

---

## What this plan will not do

**It will not add a claim to make the scoreboard longer.** Seven is what the domain has. An
eighth that restates one of the seven from a different angle would make the table look better
and prove nothing new.

**It will not close an open item by narrowing it.** The savepoint drill is not closed by
asserting that savepoints exist; the budget action is not closed by writing that it would work.
Each of the rows above says what would actually close it, and none of them says "document it".

**It will not leave a gap unwritten.** Every open item is either in the table above or in
`contracts/waivers.yaml` with a name and a date against it, and the date is enforced by a check
that goes red with no commit behind it. That is the only mechanism here that does not depend on
somebody remembering.
