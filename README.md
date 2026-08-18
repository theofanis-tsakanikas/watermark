# Watermark

**A real-time decision platform for an electricity distribution network. No decision on a
window that has not closed.**

*AWS IoT Core · Kinesis · Managed Service for Apache Flink · Iceberg on S3 · SageMaker Feature
Store · Lake Formation · Step Functions · Terraform*

> **Watermark** — the Flink term, and the thesis. A watermark is the system's claim about what
> it has seen. Every wrong decision in a streaming system is, underneath, a decision taken on
> data that had not arrived yet.

---

> **Status: complete. Every claim is proved twice — offline on a laptop with no credentials, and
> against a real AWS estate that was deployed, driven, promoted from, served from, and
> destroyed.** The scoreboard below is what a laptop proves; this block is what the account did.

**The five-step sequence, in order.** A fresh account cannot serve a model nobody has approved,
so the endpoint cannot exist on the first deploy. That is claim 5 as a property of the *order of
operations*, and it is why the sequence has five steps rather than three:

```
deploy (no endpoint) → capture → promote → deploy (endpoint) → capture
```

Both captures were green in all six jobs. From the second, with the endpoint serving and a named
human on the record:

| asserted live, by the capture rather than read off a dashboard | result |
|---|---|
| rows published with a watermark earlier than their own interval end | **0** — claim 1, checkable after the fact, on every run |
| the same day delivered twice | **3,779 values identical** — claim 2, at the offset the harness measures rather than assumes |
| the Glue Data Quality ruleset against the deployed table | **6 of 6 rules, score 1.00** |
| train/serve parity, three features, two mechanisms, no tolerance | **agreed, 0 diverged** |
| curtailment on a substation driven past its limit | **485,442 W of 450,000 W → 8 throttled**, marked as a fallback into the record |
| the live case matrix | **7 of 7** — every defect the cast declares, observed in the estate |
| the erasure | **all 6 legs confirmed against the estate**, independently of the certificate that claims them |
| the promotion gate | **approved**, and the registry records the human who took responsibility |
| consequential decisions | **20 pending, 0 actuated** — and one actuated *on a named review* |

**Claim 7 has both halves now.** Every run proves the refusal: the queue holds twenty and
actuating raises before any review exists. What needed a live endpoint and a name is the other
half — that a **named human** is the only thing that *can* actuate. Without a name the queue shows
only that nothing gets through; with one it shows what it takes for something to.

**Claim 6 is verified against the estate, not against its own certificate.** The state machine
erases and writes a certificate saying it did; `scripts/erasure_legs_live.py` asks the estate the
same questions through different services — the shred through KMS, the online store through
`GetRecord`, the lakehouse and the training sets through Athena. It found that `offline_store` was
declared in the scope, had **no branch in the state machine at all**, was absent from the
certificate, and was absent from the condition deciding whether to write one — which was a
five-way `AND` over array positions, and a hand-counted condition cannot notice a missing leg,
because the missing leg is what changes the count. Four of a subject's feature rows had survived
an erasure that certified. The branch exists now, the refusal counts against the declared set, and
`check_erasure_legs.py` holds the scope, the machine's branches and that count equal on every push.

---

**What a live estate teaches that a laptop cannot.** Roughly thirty defects were found this way,
and the pattern is one sentence: *a component nothing had ever executed*. Three maintenance jobs
had no Iceberg Spark extensions and had therefore never run. No Iceberg table had ever existed.
The reaper classified every expired resource, logged `would delete`, and returned a list — hourly,
convincingly, deleting nothing. A Glue Data Quality ruleset sat applied and attached for the life
of the lakehouse without one row ever being compared to one rule; when it finally ran, the rule
for doctrine 4 turned out to be wrong three ways, because it had never been wrong out loud. Two
feature contracts read from a table that was a catalogue entry with no writer, and an empty
Iceberg table answers every query with zero rows and no error.

Each is now refused by a check that fails on a laptop, which is the only durable form of the
lesson. And each live failure that produced a check is a failure that cannot recur.

**The instructive ones are the checks that were themselves wrong.** A `held_back` assertion passed
for weeks by reading evidence from *earlier captures* — the landing prefix accumulates. An
alignment claimed in its own docstring to choose the offset that agrees while the code counted
shared keys, which on a contiguous day is nearly flat across neighbouring offsets. A parity harness
reported an erasure that had worked as a claim-3 divergence, because SageMaker's soft delete keeps
a tombstone no re-materialisation can overtake. A green check is not evidence; a green check
somebody has watched refuse a real violation is.

**Still open, and all of it deliberate.** Two items in [contracts/waivers.yaml](contracts/waivers.yaml),
each with a name, a date and what would close it: the savepoint-restore drill, and Model Monitor
and Clarify (closed by AWS to accounts of this class). A third — the required reviewers on the
deploy environments — was open for five days and closed on 2026-08-17, found by reading
`docs/DAY-ONE.md` against the API it described and discovering the document had gone on
describing the intended state. `scripts/check_waivers.py` turns CI red on expiry
with no commit behind it, which is doctrine 6 enforced by a clock rather than by a habit.

**Cost.** A full five-step sequence — two thirty-minute captures, an endpoint served, and a
teardown — cost **USD 15.83 in a day**, against a design target of under €100. The whole nine-day
exercise, roughly fifteen captures and twenty applies while all of the above was being found, cost
**USD 115.60** tagged. Both figures are measured, not estimated, and both undercount: a cost
allocation tag takes up to 24 hours to activate, so an estate's first hours carry no tag.

---

## The problem

A distribution system operator runs ~250,000 smart meters, ~2,000 public EV chargers and 400
substations. Telemetry arrives continuously, out of order, sometimes twice, sometimes three
days late. Three decisions come out of that one stream, and they could not be more different:

| Decision | Horizon | Legal posture |
|---|---|---|
| **Curtailment** — throttle EV charging as a substation approaches its thermal limit | seconds | EU AI Act Annex III(2), argued **high-risk** |
| **Meter anomaly** — flag a meter for an inspector | hours | GDPR Art. 22 — automated decision about a person. *Not* Annex III |
| **Settlement** — hourly totals, restated when late data arrives | days | commercial, and must be reproducible to the byte |

The interesting engineering is not in any one of them. It is that a single stream feeds all
three, and each is only correct under a different definition of *"we have seen enough"*.

So the whole system is built around one boundary:

| Statistical models own | Deterministic code owns |
|---|---|
| Forecasting substation load | Whether a window is closed |
| Scoring the likelihood of meter tampering | Every published number and every aggregate |
| Ranking which inspections are worth a visit | The decision to fall back |
| — | Whether a decision may be actuated at all |

**A decision emitted from an open, stalled or incomplete window is a build failure.**

---

## The seven claims

Each is checked in CI, on a laptop, with no AWS account and no credentials.

| # | Claim | Where it is proved | Phase |
|---|---|---|---|
| **1** | **No decision comes out of a window that has not closed.** A stalled or idle watermark is detected and fails to a labelled fallback — it never starves silently. | `evals/watermark/` | 1 |
| **2** | **Replay is identical.** The same events, shuffled, duplicated and delivered late, produce byte-identical output and identical lineage hashes. | `evals/replay/` | 1 |
| **3** | **Train/serve parity.** Every feature served online equals the offline value for the same entity at the same instant — and the two values come from **two genuinely different mechanisms**, as-of SQL over Iceberg on one side and streaming materialisation into the online store on the other, sharing only the contract. One shared function compared with itself is a tautology that reports green. | `evals/parity/` | 2 |
| **4** | **No decision on a stale feature.** Past its freshness budget a feature is not served; the decision falls back, and says so. | `evals/freshness/` | 2 |
| **5** | **No model reaches an endpoint without passing the gates.** Performance, bias thresholds, a model card, a named approver. | `evals/promotion/` | 3 |
| **6** | **Erasure is complete to a declared boundary, and proved.** The lakehouse, the offline store, the online store and every training set. **Not the weights of a model trained before the request** — crypto-shredding does not reach them, and this repository does not pretend otherwise. That leg is quarantine plus retraining, with the residual window printed on the certificate. Machine unlearning is not claimed. | `evals/erasure/` | 4 |
| **7** | **A consequential decision about a person cannot be actuated automatically.** The automated path is structurally incapable of it. | `evals/oversight/` | 3 |

### The scoreboard

Every figure below is the output of a command in this repository, not a summary of one. Rows
arrive as the phases land; a row that is not here yet is work that has not happened.

| check | result |
|---|---|
| **claim 1** · no decision from an unclosed window | **7/7** — a silent substation, a stalled stream, a device three hours fast, a partition down at start-up, and the healthy day that must *not* trip any of them |
| **claim 2** · replay is identical | **5/5** — the same day shuffled under five seeds and delivered twice over produces the same 3,584 published values, the same 283 restatements and the same lineage ids |
| **claim 3** · train/serve parity | **5/5** — two independent mechanisms over one contract, compared bitemporally with no tolerance, including the planted future-leakage case |
| **claim 4** · no decision on a stale feature | **7/7** — the gate is in front of the input, and the fallback marker survives into the record |
| **claim 5** · no model reaches an endpoint ungated | **12/12** — and the gate **refuses the model fitted on the dispatch log**, for the finding in [docs/BIAS-FINDING.md](docs/BIAS-FINDING.md). Live, it approved the one fitted on randomised inspections and the registry names the human who took responsibility. Same population, same model class, same thresholds — the difference is the labels |
| **core ≡ Flink** · tier two of ADR-0003 | **green in CI**, on Linux where the wheels exist — the deployed operator chain on a MiniCluster produces the same value for every window it and the pure core both closed. Scoped to the live stream: a bounded list cannot reproduce a three-day-late batch, and the harness says so rather than absorbing it in a tolerance |
| **claim 6** · erasure to a declared boundary | **9/9** — no certificate unless every leg confirms, and the certificate names the leg deletion cannot reach. Live, **all six legs are confirmed against the estate rather than against the certificate**: one of them had no branch in the state machine at all until the independent check found rows that had survived |
| **claim 7** · no automatic decision about a person | **8/8** — the contract does not load and the actuation type cannot be constructed |
| `make gate-proof` | **40 refused, 0 accepted, 0 stale** |
| `make policy` | **24 principal-resource pairs** — every reachable set exact and every closed path closed |
| `make seed-check` | **4,312 deliveries** reproduce `recordings/day.json` exactly — 3,584 published, 283 restated, 284 quarantined, net restatement **+2,261 Wh** |
| `make test` | **341 passing**, offline, credential-free, no JVM, in about two seconds |
| `terraform validate` | **6/6 layers** against real provider schemas |
| `checkov` | **0 findings**, 64 deliberate exceptions each carrying a written reason beside the resource |
| `make preflight` | **37 passed, 0 failed, 0 skipped** |
| **the declared cases** · offline | **11/11** — every defect the cast declares is observed in the generated day, and a cohort that is declared and unchecked fails the run |
| **the declared cases** · against the estate | **7/7** — the same questions asked of the deployed system rather than of the core |
| **the settlement path** · doctrine 4 and its contract | **8/8** — the third decision contract, whose safe state is the inverse of curtailment's |
| `check_waivers` | **2 live, none expired** — doctrine 6, enforced by a clock rather than by a habit; a third was closed by being read against the API it described |
| `check_feature_sources` | **3 features**, every column present in a table something writes |
| `check_erasure_legs` | **6 legs**, named identically by the scope, the state machine's branches and the count the refusal compares against |
| the five-step sequence, live | **both captures green in all six jobs** — deploy, capture, promote, deploy with the endpoint, capture |

Two rows are worth reading twice.

**Claim 5 refuses the model this repository trained.** Not a threshold that happens to be
tight — a finding. Precision measured 1000/1000 in the most deprived tercile against 181/1000
in the least, because every true case there was confirmed by an inspector and almost none
elsewhere was. The model looks excellent exactly where the dispatch log is densest. The gate
had been written to catch the *opposite* shape and would have called it a pass;
[docs/BIAS-FINDING.md](docs/BIAS-FINDING.md) has the numbers, what changed, and what is still
not fixed.

**`gate-proof` is the row to read first.** A suite tells you the code does what it does; this
copies the repository, plants forty real violations, and requires the *named* gate to
refuse each one *for the right reason* — a non-zero exit is not evidence, and a mutation whose
target has moved is reported STALE rather than passed.

Several of the thirty-nine are mistakes this project actually made. The telemetry read that
listed one prefix and reported three of four substations as silent. The replay offset rounded
onto a grid the two runs did not share. The Glue job importing a name its Python 3.10 runtime
does not have. Writing the mutation is also how the gaps in the harness itself were found: six
checks had no mutation against them at all, and eleven more under `scripts/` were never run by
one — so the coverage rule is now mechanical, and a new check ships red until something attacks
it.

**Tier two is green, and one leg of it is not.** ADR-0003's second tier runs the real PyFlink
job on a MiniCluster and asserts it produces the same bytes as the pure core. It cannot execute
on the machine this was written on — `apache-flink` requires `apache-beam`, which has no wheel
for Python 3.12 on arm64 macOS, recorded with its date in
[docs/AWS-CONSTRAINTS.md](docs/AWS-CONSTRAINTS.md) — so it runs in CI on Linux with
`WATERMARK_REQUIRE_FLINK=1`, which turns a missing runtime into a failure rather than a skip.

What is *not* proved is equivalence **across a restart**: cancel with a savepoint, resume, and
produce the same bytes over the break. That needs a harness rather than an assertion — something
to hold a job open and bring it back — and until it exists the test is skipped. It is WV-001 in
[contracts/waivers.yaml](contracts/waivers.yaml) with a date on it, which is the difference
between a gap somebody chose and a gap nobody noticed.

---

## The doctrine

Seven rules that settle every "what happens when it goes wrong" question here. The first one
is the project's defining argument and it is the opposite of the answer most compliance
engineering gives.

1. **The safe state is the conservative deterministic action, not silence.** In a report
   factory, refusing to publish is safe. On a grid it is not: the substation still overloads
   while nobody is deciding. Every decision path declares a *fallback rule* — deterministic,
   conservative, computable with no model and no fresh features.
   ([ADR-0001](docs/adr/0001-the-safe-state-is-a-conservative-action.md))
2. **A fallback is visible all the way to the end.** Into the actuator, the record and the
   dashboard. A fallback that looks like a model decision is worse than an outage.
3. **Anything with a significant effect on a person waits for a human.** Not a review after
   the fact — the actuation path does not exist without a recorded human decision.
4. **A correction never erases what was previously stated.** Late data restates. The prior
   value, the reason and the delta stay recoverable.
5. **Nothing approves itself.** No model, no pipeline, no service principal.
6. **Exceptions expire.** On expiry the finding returns and CI goes red again.
7. **One door has no key.** A train/serve parity mismatch cannot be overridden — it means the
   number that trained the model and the number in production are different things, so nobody,
   including the approver, knows what they would be approving.

---

## Running it

```bash
make install       # venv + editable install
make test          # full suite, offline, no JVM, under a second
make claims        # every claim gate that exists today
make gate-proof    # break each gate on purpose; each must be refused, for the right reason
make wiring        # the offline stand-ins for a plan nobody can run without credentials
make tf-validate   # every layer against real provider schemas, no backend, no credentials
make preflight     # all 18: correctness, consistency, deployability
```

Requires Python 3.12+. No AWS account, no credentials, no network.

There is a second, slower tier — `make test-flink` — which runs the real PyFlink job on a local
MiniCluster and asserts it produces byte-identical output to the pure core. It needs a JVM, it
is a separate CI job, and it arrives with the streaming adapter in phase 1. The reasoning for
splitting them, and for why a skipped tier must fail rather than pass in CI, is
[ADR-0003](docs/adr/0003-the-pure-core-boundary.md).

---

## Repository layout

| Path | Purpose |
|---|---|
| [`src/watermark/core/`](src/watermark/core/) | **The pure stream core** — standard library only, no clock, no cloud. Claims 1–4 live here |
| [`src/watermark/gates/`](src/watermark/gates/) | The acceptance gates, one module per claim |
| [`scripts/gate_proof.py`](scripts/gate_proof.py) | Breaks every gate on purpose and demands the named refusal |
| [`docs/adr/`](docs/adr/) | Every decision that would otherwise be re-argued next month |
| [`infra/`](infra/) | Terraform. `bootstrap/` applies from a laptop; every other layer only from a gated workflow |

| [`contracts/entities/`](contracts/entities/) | **The source of truth** for reference data — YAML, validated at load; personal data without a declared purpose does not load |
| [`data/`](data/) | The synthetic operator: a fixed cast and a seeded day with every pathology in the scenario, present on purpose and labelled |
| [`evals/`](evals/) | The claim harnesses — labelled situations, scored, credential-free |
| [`recordings/`](recordings/) | The golden day. `make seed-check` proves the generator still reproduces it |

| [`streaming/`](streaming/) | The PyFlink adapter. It moves records and decides nothing — no semantic literal, enforced |
| [`queries/`](queries/) | SQL for settlement and reconciliation. Parameters bound, never interpolated |
| [`pipelines/dbt/`](pipelines/dbt/) | dbt-athena over the gold layer, with the two tests that matter more than the models |
| [`.github/workflows/`](.github/workflows/) | CI on every push; `deploy`, `destroy` and `capture` behind a `deploy` environment with a required reviewer — the only way anything reaches AWS |

Their shape is fixed in [CLAUDE.md](CLAUDE.md) and the order they arrive in is
[PLAN.md](PLAN.md).

## Deploying it, and how it has been

Everything needed is here: six Terraform layers, a dbt project, an application package, and
four gated workflows. `deploy.yml` re-runs the whole of CI against the exact ref being
deployed — not "CI passed on main last night" — assumes a role through OIDC with no secret
anywhere, applies the layers in dependency order, and prints each plan before applying it.
`destroy.yml` takes it down in reverse order and deliberately does *not* require CI to pass,
because the moment somebody most needs to tear an estate down is the moment something is
broken. `capture.yml` is the only thing that starts the three expensive resources, and its stop
step runs `if: always()`. `promote.yml` is the one that puts a model in front of traffic, and it
refuses to without a named human and without train/serve parity holding.

`capture.yml` also takes `-f from_stage`, the way `deploy.yml` takes `-f layer=`: a capture that
failed at the fifth job continues rather than starting again. Because the danger there is not the
resuming but the *quoting*, every run's summary opens by saying which it was — a whole capture, or
a resumed one that **may not be cited as evidence**. Same discipline as the fallback marker that
has to reach the record.

**All four have been dispatched.** `infra/bootstrap/` was applied from a laptop on 2026-08-10
— the one layer whose own design always said so — and every other layer went up through
`deploy.yml`, was driven by `capture.yml`, promoted from by `promote.yml`, and came down through
`destroy.yml`. Nothing in this repository has ever been applied from a console or from a laptop
shell, and the estate has been stood up and torn down enough times that the teardown is verified
from the CLI afterwards as a matter of routine.

`docs/DECISIONS.md` 15 argued that it never would be, because every claim is provable offline
by construction and a live run would produce a screenshot rather than a proof. **Decision 17
retracts it**, and names the error: proving the logic offline says nothing about whether the
estate that would carry it can exist. Those are two propositions and only one had been checked.
The run found four design errors that no schema check can reach — among them that PyFlink
cannot emit a custom watermark, and that a green CI run had moved zero records. Decision 15 is
kept in full rather than deleted, because an argument that was wrong is worth being able to
read.

### The documents that decide things

| Document | What it settles |
|---|---|
| [docs/SCENARIO.md](docs/SCENARIO.md) | The domain, its volumes, and the pathologies the synthetic data must contain on purpose |
| [docs/REGULATORY.md](docs/REGULATORY.md) | The legal posture, argued rather than asserted, every citation read in source and dated |
| [docs/AWS-CONSTRAINTS.md](docs/AWS-CONSTRAINTS.md) | Service facts that make a design impossible rather than merely different — verified and dated |
| [docs/DECISIONS.md](docs/DECISIONS.md) | What was settled before the first line of code |
| [docs/DAY-ONE.md](docs/DAY-ONE.md) | The manual work that has no API, written down before it is done |
| [ADR-0001](docs/adr/0001-the-safe-state-is-a-conservative-action.md) | Why silence is not the safe state on a grid |
| [ADR-0002](docs/adr/0002-iceberg-on-s3-over-s3-tables.md) | Iceberg on S3 over S3 Tables, and the two facts that would reverse it |
| [ADR-0003](docs/adr/0003-the-pure-core-boundary.md) | What the pure core guarantees, what the adapter may not decide, and where a JVM is needed |
| [ADR-0004](docs/adr/0004-two-mechanism-parity.md) | How claim 3 avoids being a tautology that reports green |
| [ADR-0005](docs/adr/0005-two-reproducibility-tiers.md) | What "reproducible" is promised to mean, and where it stops |
| [ADR-0006](docs/adr/0006-clarify-runs-but-does-not-vote.md) | Why the standard bias metric is reported and not obeyed — and why it could not run at all |
| [ADR-0007](docs/adr/0007-the-framework-carries-records-not-semantics.md) | What the live run proved PyFlink cannot do, and why that made the design stronger |
| [ADR-0008](docs/adr/0008-the-writer-creates-the-iceberg-table.md) | Why Terraform cannot create an Iceberg table, and what owns the schema instead |
| [ADR-0009](docs/adr/0009-a-key-per-subject-and-what-it-costs.md) | One KMS key per data subject, why it is a master key and not a data key, and the fleet-scale ceiling it has |

---

## Cost posture

**The design target for one full capture with teardown is under €100.** A complete five-step
sequence — two thirty-minute captures, a promotion, an endpoint served, and a teardown — measured
**USD 15.83 in a day**. The whole nine-day exercise, roughly fifteen captures and twenty applies
while most of the defects in this repository were being found, measured **USD 115.60** tagged.

Both are measurements rather than estimates, and both undercount: a cost allocation tag takes up
to 24 hours to activate, so an estate's first hours carry no tag. The two halves belong together —
a measurement quoted without the reason it is low is not more honest than the design constraint it
replaced.

Nothing can be applied outside a gated workflow. Every resource carries a `watermark:expires-at`
tag, and `infra/bootstrap/cost.tf` holds a budget whose action detaches the deploy role at its
threshold.

**The budget action has never fired** — the spend never approached the ceiling — so it remains a
designed control rather than a demonstrated one, and this section says so rather than implying
otherwise. **The reaper is a different case, and worth reading.** It was a designed control that
was not even that: it classified every expired resource, logged `would delete`, and returned a
list, hourly, having deleted nothing, because the mapping from resource type to deletion API was
never called. It deletes now, behind an explicit mode, with a test over every branch that deletes
and every branch that must not — including that a resource with no expiry is reported rather than
removed, and that `never` means never.

The three expensive things — Managed Flink KPUs, the SageMaker Feature Store online store and any
real-time endpoint — are confined to deliberate bounded blocks rather than left standing. A
`scripts/check_cost_envelope.py` refuses a *design* that could not come in under the target, which
is the half a rate card can answer; the figures above are the half only a bill can.

---

## Licence

MIT — see [LICENSE](LICENSE). Engineering rules live in [CLAUDE.md](CLAUDE.md).
