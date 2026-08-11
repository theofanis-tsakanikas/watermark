# CLAUDE.md — Watermark

**A real-time decision platform for an electricity distribution network. No decision on a
window that has not closed.**

AWS-native: IoT Core · Kinesis · Managed Service for Apache Flink · Iceberg on S3 ·
SageMaker (Feature Store · Pipelines · Model Registry · Clarify · Model Monitor · endpoint) ·
Lake Formation · Step Functions · Terraform

> **Watermark** — the Flink term, and the thesis. A watermark is the system's claim about
> what it has seen. Every wrong decision in a streaming system is, underneath, a decision
> taken on data that had not arrived yet.

---

## Read this first, every session

This document is the single source of truth for what this project is. Alongside it:

| File | What it holds |
|---|---|
| `PLAN.md` | The four phases, task by task, with the definition of done for each |
| `docs/SCENARIO.md` | The domain — data model, volumes, the failure modes that must be handled |
| `docs/REGULATORY.md` | The legal posture, argued rather than asserted, with what still needs verifying |
| `docs/DECISIONS.md` | Decisions already locked before the first line of code, and why |

**Language:** all repository content is written in **English** (the audience is
international, as with every other project in this portfolio). Conversation with the author
is in **Greek**.

---

## What this system does (the mental model — keep it in mind every session)

A distribution system operator runs ~250,000 smart meters, ~2,000 public EV chargers and
400 substations. Meter and charger telemetry arrives continuously, out of order, sometimes
days late, sometimes twice. Three decisions come out of that stream:

| Decision | Horizon | Legal posture |
|---|---|---|
| **Curtailment** — throttle EV charging when a substation approaches its limit | seconds | **AI Act Annex III(2)** — safety component in the management of electricity supply → argued **high-risk** |
| **Meter anomaly / tampering flag** → dispatch an inspector | hours | **GDPR Art. 22** — automated decision with significant effect on a person. *Not* Annex III |
| **Settlement** — hourly consumption totals, restated when late data arrives | days | commercial — but must be reproducible to the byte |

That the first is high-risk and the second is not is **deliberate and structural**. The
platform must treat them differently, and that difference must be enforced in code, not
described in a document. A single code path with a flag is not the answer; see
`docs/REGULATORY.md`.

### The boundary this system is built around

| Statistical models own | Deterministic code owns |
|---|---|
| Forecasting substation load | Whether a window is closed |
| Scoring the likelihood of meter tampering | Every published number and every aggregate |
| Ranking which inspections are worth a visit | The decision to fall back |
| — | Whether a decision may be actuated at all |

**A decision emitted from an open, stalled or incomplete window is a build failure.** That
is the project, in one sentence.

---

## The seven claims

Everything here exists to make one of these provable **in CI, on a laptop, with no AWS
account and no credentials**. If a change does not serve one of them, question it.

| # | Claim | Proved by |
|---|---|---|
| **1** | **No decision comes out of a window that has not closed.** A stalled or idle watermark is detected and fails to a labelled fallback — never starves silently. | `evals/watermark/` |
| **2** | **Replay is identical.** The same events, shuffled, duplicated and delivered late, produce byte-identical outputs and identical lineage hashes. | `evals/replay/` |
| **3** | **Train/serve parity.** Every feature served online equals the offline value computed for the same entity at the same instant. **The two values must come from two genuinely different mechanisms** — as-of SQL over Iceberg on one side, streaming materialisation into the online store on the other — sharing only the contract. One shared function compared with itself is a tautology that reports green. CI fails on any divergence. | `evals/parity/` |
| **4** | **No decision on a stale feature.** A feature past its freshness budget is never served; the decision falls back deterministically and carries the fallback marker to the end. | `evals/freshness/` |
| **5** | **No model reaches an endpoint without passing the gates.** Performance, bias thresholds, a model card and a named approver — the promotion gate refuses, and refuses for the stated reason. | `evals/promotion/` |
| **6** | **Erasure is complete to a declared boundary, and proved.** An erasure request removes the subject from the lakehouse, the offline store, the online store and every training set. A model trained before the request keeps the subject's contribution in its weights: crypto-shredding does not reach it and this repository does not pretend otherwise. That leg is satisfied by quarantine plus retraining on a stated schedule, with the residual window declared on the face of the certificate. **Machine unlearning is not claimed.** The system refuses to report "erased" unless every leg is confirmed. | `evals/erasure/` |
| **7** | **A consequential decision about a person cannot be actuated automatically.** The automated path is structurally incapable of it, not merely configured not to. | `evals/oversight/` |

**Claim 1 is the one the project is named after. Claim 3 is the one nothing else in the
portfolio proves. Claim 6 is the hardest.**

On top of the seven: `make gate-proof` copies the repo, plants a *real* violation, and fails
unless the named gate refuses it **for the right reason** — same three rules as Attestor:
every gate must be green first; a non-zero exit is not evidence (the *named* check must
report the failure); a mutation whose target has moved is reported **STALE**, not passed.

---

## The doctrine — what happens when it goes wrong

Seven rules that decide every failure-handling question. When a new control is added, work
out its answer to each before writing it. Each becomes an ADR.

1. **The safe state is the conservative deterministic action, not silence.** This is the
   single most important difference from Attestor. In a report factory, refusing to publish
   is safe. On a grid it is not: the substation still overloads while nobody is deciding.
   Every decision path therefore declares a *fallback rule* — deterministic, conservative,
   computable without a model and without fresh features — and "no output" is only the safe
   state where an action has no physical consequence.
2. **A fallback is visible all the way to the end.** A decision produced by fallback carries
   that marker into the actuator, the record and the dashboard. A fallback that looks like a
   model decision is worse than an outage, because it is silent and it trains someone to
   trust it.
3. **Anything with a significant effect on a person waits for a human.** Not a review after
   the fact — the actuation path does not exist without a recorded human decision.
4. **A correction never erases what was previously stated.** Late data restates; it does not
   overwrite. The prior value, the reason and the delta are recoverable.
5. **Nothing approves itself.** No model, no pipeline, no service principal may approve a
   promotion, grant an exception or classify its own risk level.
6. **Exceptions expire.** On expiry the finding returns and CI goes red again.
7. **One door has no key.** A train/serve parity mismatch cannot be overridden. It means the
   number that trained the model and the number in production are different things, so
   nobody — including the approver — knows what they would be approving. Having exactly one
   unopenable door is what keeps the other six honest.

---

## Non-negotiable engineering rules

**Framework-free core.** All stream logic — windowing, deduplication, point-in-time joins,
lateness handling, fallback rules, gates — lives in `src/watermark/core/` as **pure
functions over plain data structures, importing no Flink, no boto3, no AWS SDK**. The
PyFlink job in `streaming/` is a thin adapter that calls them. This is not stylistic: it is
the only way claims 1–4 are provable on a laptop with no cluster. A test that needs a Flink
cluster to run is a test that will not run.

**Offline is the default.** The full suite, every eval, every gate and every
`terraform validate` runs with no AWS account. Cloud is for capturing proof, not for
validating logic.

**IaC only.** Every cloud resource in Terraform. No console deployments, ever. Day-1 manual
work that has no API is recorded in `docs/DAY-ONE.md`, never silently done.

**Bootstrap is local, everything else is CI.** `infra/bootstrap/` (remote state + the OIDC
role CI assumes) applies once from a laptop. Every other layer applies **only** from a gated
workflow. A layer that can be applied from a laptop is a layer that will drift.

**No long-lived access keys. Ever.** OIDC for CI, execution roles for services, X.509 per
device, Secrets Manager for the rest. `gitleaks` gates every push.

**Terraform state is isolated per layer.** Cross-layer references are `outputs` → `data`
sources. Never a remote state read across layers.

**Deterministic first.** Before adding a model call, answer: *is there exactly one correct
answer here?* If yes, it is code. A model step with repair logic underneath it is an
anti-pattern — delete the model step and keep the code.

**Fail closed on safety and compliance; fail open on quality.** A missing consent record
means no processing. A slow enrichment lookup means an unenriched record, logged.

**Every gate is attacked.** No gate ships without a `gate-proof` mutation that breaks it.

**Done = runs + tested.** Generated-but-unrun code is not done.

**Every regulatory claim is traced or it is deleted.** Same rule as the site's framework: if
a statement in a README or a doc cannot be traced to a named article of a named instrument,
it does not belong. Verify before writing; record the date of verification.

---

## Repository layout

```
watermark/
├── contracts/                  # THE SOURCE OF TRUTH — YAML, data, never imported by name
│   ├── entities/               #   reference entities and their SCD-2 rules (meter, customer, tariff, substation)
│   ├── features/               #   one file per feature: definition, window, freshness budget, owner
│   └── decisions/              #   one file per decision: inputs, model, fallback rule, actuation policy, legal posture
├── queries/                    # SQL for offline/settlement resolution — parameters bound, never interpolated
├── data/                       # synthetic generator + seeds (deterministic, seeded, committed)
├── streaming/                  # PyFlink jobs — thin adapters over src/watermark/core
├── src/watermark/
│   ├── core/                   # PURE stream logic: windows, watermarks, dedup, PIT join, lateness
│   ├── features/               # feature registry, offline/online resolution, parity harness, freshness
│   ├── decisions/              # decision engine, fallback rules, actuation policy, oversight queue
│   ├── models/                 # training, evaluation, bias analysis, the promotion gate
│   ├── gates/                  # the acceptance gates (one module per claim)
│   ├── erasure/                # crypto-shredding orchestration + the completeness proof
│   ├── lineage/                # lineage ids, restatement records, as-of resolution
│   ├── policy/                 # Lake Formation tag policy, authored and evaluated offline
│   └── observability/          # OTEL spans, cost meter per decision and per meter
├── evals/                      # the seven claim harnesses — labelled, credential-free
├── recordings/                 # golden outputs; every generated total reproduces its recording exactly
├── infra/
│   ├── bootstrap/              # LOCAL apply only — state backend + CI OIDC role
│   ├── foundation/             # VPC, KMS (incl. per-subject key hierarchy), S3, budget guard, TTL reaper
│   ├── streaming/              # IoT Core, Kinesis, Managed Flink, Glue Schema Registry
│   ├── lakehouse/              # Iceberg tables, Glue Catalog, Athena, Glue Data Quality
│   ├── ml/                     # SageMaker: Feature Store, Pipelines, Registry, Clarify, Model Monitor, endpoint
│   └── governance/             # Lake Formation tags and grants, Step Functions, observability
├── pipelines/                  # backfill, restatement, erasure orchestration, dbt-athena models
├── scripts/                    # gate_proof.py, preflight.py, tf_validate.py, check_*.py
├── docs/
│   ├── adr/                    # architecture decision records
└── .github/workflows/          # ci.yml (every PR) + gated deploy.yml / destroy.yml
```

---

## The contract layer — read before touching anything

Three contract families, all YAML under `contracts/`, all **data**. No Python imports a
contract by name.

**A feature contract** declares what the feature means, its window and grain, its
**freshness budget** (how stale is too stale), where its offline and online values come
from, and its owner. A feature with no freshness budget cannot load — that is what makes
claim 4 mechanical rather than a matter of remembering.

**A decision contract** declares its inputs, its model (or none), its **fallback rule**, its
actuation policy (automatic / human-gated), its legal posture, and the record it must
write. A decision contract with `effect: significant_on_person` and `actuation: automatic`
must fail to load. Claim 7 is enforced at load time, before any runtime path exists.

**An entity contract** declares the reference data and its SCD-2 rules, so a point-in-time
join has one definition rather than one per query.

Changing a feature's window, grain or definition is a **restatement**: it requires a
`supersedes` entry, and CI demands the prior-period note.

---

## Cost controls — always active

> **The estate has been deployed, driven and destroyed.** Decided 2026-08-11; see
> `docs/DECISIONS.md` 17, which supersedes 15. Every layer went up through the gated workflow,
> the scenario ran through it, and it all came down again; `infra/bootstrap/` was applied from a
> laptop on 2026-08-10. The controls below are therefore both a *design* discipline and a thing
> that has been tested against a real bill.
>
> **A euro figure or a wall-clock time may be written only if it was measured, is labelled as
> measured, and carries what it excludes.** The exercise's tagged spend was USD 12.35, and it
> undercounts: a cost allocation tag takes up to 24 hours to activate, so the estate's first
> hours carry no tag. Quoting the number without that sentence is the failure mode here.

- **Nothing is applied outside a gated workflow.** No exceptions.
- Every resource carries `watermark:expires-at`; a scheduled reaper destroys what expired.
- An AWS Budget action disables the deploy role at its threshold.
- **The three expensive things are Managed Flink KPUs, the SageMaker Feature Store online
  store, and any real-time endpoint.** They live in deliberate, bounded blocks: stand up,
  drive the scenario, capture, destroy. They are never left standing.
- Cost per decision and cost per meter are first-class metrics, not an afterthought.
- Target for a full live capture with teardown: **under €100**. If a design pushes past it,
  the design is wrong before the budget is.

---

## Git workflow

Conventional Commits: `<type>(<scope>): <description>`
Types: `feat | fix | infra | docs | refactor | test | chore`
Scopes: `contracts | core | features | decisions | models | gates | erasure | lineage | streaming | infra | ci | evals`

One logical change per commit. Never commit credentials, real account ids, or anything but
synthetic data.

---

## Before any change — checklist

- Which of the seven claims does this serve?
- Is there exactly one correct answer here? Then it is code, not a model.
- Can it be validated with no AWS account? If not, why not?
- If it is a gate: is there a `gate-proof` mutation that breaks it and proves it bites?
- If it touches a contract: does the change imply a restatement?
- If it touches a decision path: what is the fallback, and is the fallback marker carried to
  the end?
- If it states a legal fact: which article, which instrument, verified when?
