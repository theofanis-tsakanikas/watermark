# Watermark

**A real-time decision platform for an electricity distribution network. No decision on a
window that has not closed.**

*AWS IoT Core · Kinesis · Managed Service for Apache Flink · Iceberg on S3 · SageMaker Feature
Store · Lake Formation · Step Functions · Terraform*

> **Watermark** — the Flink term, and the thesis. A watermark is the system's claim about what
> it has seen. Every wrong decision in a streaming system is, underneath, a decision taken on
> data that had not arrived yet.

---

> **Status: all four phases complete. Every claim proved offline, and the estate has been
> deployed to a real AWS account, driven, and destroyed.** The scoreboard below lists what is
> provable on a laptop with no credentials; a row appears only when the command that produces
> it exists.
>
> **What the live run proved, and what it did not.** Claims 1, 5 and 6 were exercised against
> real infrastructure: every published window carried its watermark status, the model
> registered `PendingManualApproval`, and the erasure state machine **refused to certify** —
> the correct answer, live. Three things were *not* exercised and are gaps rather than results:
> **claim 2 end-to-end**, because the Glue merge job was blocked on a missing IAM read
> (now fixed, not yet re-run); **the endpoint and Model Monitor**, because both require a model
> a human has approved and none was; and the `held_back` / `stalled` / `starved` watermark
> states, which are proved offline but were never induced in the cloud.
>
> The run is also where four design errors were found that no schema check could reach — among
> them that PyFlink cannot emit a custom watermark, and that a green CI run had moved zero
> records. `docs/DECISIONS.md` 17 has the list and retracts the decision that said a live run
> would add nothing.
>
> **Cost.** The tagged spend for the whole exercise was **USD 12.35**, against a design target
> of under €100. It undercounts: a cost allocation tag takes up to 24 hours to activate, so the
> estate's first hours are untagged. Both halves of that belong to the number.

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
| **claim 5** · no model reaches an endpoint ungated | **12/12** — and **the shipped model is refused**, for the finding in [docs/BIAS-FINDING.md](docs/BIAS-FINDING.md) |
| **claim 6** · erasure to a declared boundary | **9/9** — no certificate unless every leg confirms, and the certificate names the leg deletion cannot reach |
| **claim 7** · no automatic decision about a person | **8/8** — the contract does not load and the actuation type cannot be constructed |
| `make gate-proof` | **21 refused, 0 accepted, 0 stale** |
| `make policy` | **24 principal-resource pairs** — every reachable set exact and every closed path closed |
| `make seed-check` | **4,312 deliveries** reproduce `recordings/day.json` exactly — 3,584 published, 283 restated, 284 quarantined, net restatement **+2,261 Wh** |
| `make test` | **213 passing**, offline, credential-free, no JVM, under a second |
| `terraform validate` | **6/6 layers** against real provider schemas |
| `checkov` | **0 findings**, 48 deliberate exceptions each carrying a written reason beside the resource |
| `make preflight` | **30 passed, 0 failed, 0 skipped** |
| core↔Flink equivalence | **not yet observed green anywhere** — see below |

Two rows are worth reading twice.

**Claim 5 refuses the model this repository trained.** Not a threshold that happens to be
tight — a finding. Precision measured 1000/1000 in the most deprived tercile against 181/1000
in the least, because every true case there was confirmed by an inspector and almost none
elsewhere was. The model looks excellent exactly where the dispatch log is densest. The gate
had been written to catch the *opposite* shape and would have called it a pass;
[docs/BIAS-FINDING.md](docs/BIAS-FINDING.md) has the numbers, what changed, and what is still
not fixed.

**`gate-proof` is the row to read first.** A suite tells you the code does what it does; this
copies the repository, plants twenty-one real violations, and requires the *named* gate to refuse
each one *for the right reason*. Four of the twenty-one are mistakes this project actually made and
the harness caught.

**One row is deliberately absent from the scoreboard, and the reason has changed.** ADR-0003's
tier two runs the real PyFlink job on a MiniCluster and asserts it produces the same bytes as
the pure core. It cannot be executed on the machine this was written on — `apache-flink`
requires `apache-beam`, which has no wheel for Python 3.12 on arm64 macOS, recorded with its
date in [docs/AWS-CONSTRAINTS.md](docs/AWS-CONSTRAINTS.md).

**That is no longer what is stopping it.** The CI job on Linux installs the runtime and reaches
the test, which then fails on an unwritten harness: `_run_on_mini_cluster` raises
`NotImplementedError` rather than returning an empty collection, because an equivalence test
that compares nothing against nothing passes and looks exactly like one that worked. So the
honest statement is not "we cannot run it here" but **"the harness has not been written"** —
a smaller excuse, and the true one. The job runs with `WATERMARK_REQUIRE_FLINK=1` so a missing
runtime would be a failure rather than a skip, and it stays off this table until somebody has
watched it pass. A check nobody has seen go green is indistinguishable from one that cannot.

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
three gated workflows. `deploy.yml` re-runs the whole of CI against the exact ref being
deployed — not "CI passed on main last night" — assumes a role through OIDC with no secret
anywhere, applies the layers in dependency order, and prints each plan before applying it.
`destroy.yml` takes it down in reverse order and deliberately does *not* require CI to pass,
because the moment somebody most needs to tear an estate down is the moment something is
broken. `capture.yml` is the only thing that starts the three expensive resources, and its stop
step runs `if: always()`.

**All three have been dispatched.** `infra/bootstrap/` was applied from a laptop on 2026-08-10
— the one layer whose own design always said so — and every other layer went up through
`deploy.yml`, was driven by `capture.yml`, and came down through `destroy.yml`. Nothing in this
repository has ever been applied from a console or from a laptop shell.

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

---

## Cost posture

**The design target for one full capture with teardown is under €100. The measured tagged spend
was USD 12.35** — and that number undercounts, because a cost allocation tag takes up to 24
hours to activate and the estate's first hours therefore carry no tag. The two halves belong
together: a measurement quoted without the reason it is low is not more honest than the design
constraint it replaced.

Nothing can be applied outside a gated workflow. Every resource carries a
`watermark:expires-at` tag that a scheduled reaper enforces, and an AWS Budget action disables
the deploy role at its threshold. **Neither has ever fired** — the workflow destroyed everything
well inside its expiry, and the spend never approached the ceiling — so both remain designed
controls rather than demonstrated ones, and this section says so rather than implying otherwise.

The three expensive things — Managed Flink KPUs, the SageMaker Feature Store online store and
any real-time endpoint — are confined to deliberate bounded blocks rather than left standing.
That discipline is why the figure above is USD 12.35 and not a story about a forgotten cluster.

---

## Licence

MIT — see [LICENSE](LICENSE). Engineering rules live in [CLAUDE.md](CLAUDE.md).
