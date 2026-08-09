# PLAN — building Watermark end to end

Four phases. **Each one leaves the repository in a state that can be shown to an
interviewer.** There is no point at which the project is half a thing.

Work top to bottom. Do not start a phase's cloud work before its offline work is green.

Definition of done, everywhere: *the code runs, it is tested, the tests run offline, and if
it is a gate there is a `gate-proof` mutation that breaks it.*

---

## Phase 0 — Foundations (before any feature)

- [x] `pyproject.toml`, ruff config, pytest config, `Makefile` with the help-target pattern
      from Attestor. The venv-or-ambient-interpreter handling in Attestor's Makefile is there
      because of a real CI failure — copy the approach, not blindly the file.
- [x] `.github/workflows/ci.yml`: lint, test, gitleaks, `terraform validate`, checkov.
      CI exists from commit one; a suite added later is a suite shaped to pass.
- [x] `docs/adr/0001-the-safe-state-is-a-conservative-action.md` — doctrine rule 1. This is
      the project's defining argument and it should be written before the code that assumes it.
- [x] Verify every citation in `docs/REGULATORY.md` against source text; stamp the file.
- [x] Decide S3 Tables vs Iceberg-on-S3 with the current docs open; ADR either way.
- [x] **ADR-0003 — the pure-core boundary and how core↔Flink equivalence is established.**
      What exactly the core guarantees; what the adapter is structurally forbidden to decide
      (enforced by `scripts/check_core_is_pure.py`); where the equivalence test runs, given
      that it needs a JVM. Two test tiers: `make test` stays pure and instant,
      `make test-flink` carries the MiniCluster and runs as its own CI job.
- [x] **ADR-0004 — the two-mechanism parity design.** Written now, not in Phase 2. A shared
      contract, two independent execution paths, and the leakage cases planted for the harness
      to catch. Deciding this after the feature code exists means rewriting the feature code.
- [x] **`docs/AWS-CONSTRAINTS.md`** — the service facts that shape the design, each verified
      against current documentation and dated: SageMaker Feature Store's record-identifier and
      event-time requirements and its online/offline consistency model · Managed Service for
      Apache Flink's Python application packaging, KPU model and savepoint behaviour · Kinesis
      on-demand vs provisioned and the shard model under burst · Iceberg maintenance and
      compaction · Lake Formation's integration with whichever table format wins ADR-0002.
      This is what de-risks the Terraform, not writing the Terraform early.
- [x] `infra/bootstrap/` — state backend + CI OIDC role. Written, validated, **not applied**.

**Done when:** `make test` and `make lint` are green on an empty-but-real skeleton, and CI
runs them on a pull request.

---

## Phase 1 — The stream is correct

*Unlocks claims 1 and 2. This phase alone is a publishable project.*

- [ ] `data/` — the seeded synthetic generator, with every pathology listed in
      `docs/SCENARIO.md`. Committed, deterministic, no network.
- [ ] `contracts/entities/` — meter, customer, tariff, substation, meter assignment, with
      SCD-2 rules. Loader + validation + cross-checks.
- [ ] `src/watermark/core/` — the pure logic, no Flink import anywhere in it:
      normalisation across firmware schema variants · deduplication on
      `(meter_id, interval_start, payload_hash)` · event-time windowing · watermark
      generation with idle-source detection · allowed lateness with a side output ·
      clock-skew quarantine with a reason · point-in-time SCD-2 join.
- [ ] `src/watermark/lineage/` — a lineage id minted at ingestion and carried to every
      downstream artefact; restatement records (prior value, new value, cause, delta).
- [ ] `evals/watermark/` — **claim 1**. The labelled cases: an idle substation, a stalled
      watermark, a burst, a window that must not close, a window that must. Each expects a
      specific outcome, not "no exception".
- [ ] `evals/replay/` — **claim 2**. Shuffle, duplicate and delay the same event set; assert
      byte-identical outputs and identical lineage hashes.
- [ ] `recordings/` — golden outputs; a `seed-check` target proving every generated total
      reproduces its recording exactly.
- [ ] `streaming/` — the PyFlink job as a thin adapter. Prove locally that it produces the
      same output as the pure core on the same input; that equivalence is itself a test.
- [ ] Settlement resolution + restatement pipeline: the 3-day-late batch changes a published
      total, and the prior value survives.
- [ ] `infra/foundation/` and `infra/streaming/` — Terraform, validated, not applied.

**Done when:** claims 1 and 2 pass offline, `gate-proof` breaks both and they refuse for the
named reason, and the late batch restates rather than overwrites.

---

## Phase 2 — Features that can be trusted

*Unlocks claims 3 and 4. This is where the project becomes an AI data engineering project.*

- [ ] `contracts/features/` — one file per feature: definition, window, grain, **freshness
      budget**, purpose (GDPR Art. 5), owner. A feature without a freshness budget or a
      declared purpose must fail to load, with a test asserting it.
- [ ] `src/watermark/features/` — offline resolution (as-of SQL over Iceberg) and online
      resolution (streaming materialisation into the Feature Store) from **one contract and
      two deliberately different mechanisms**. The contract is shared; the execution is not.
      Collapsing them into one shared function would make claim 3 compare code with itself
      and report green — see ADR-0004.
- [ ] `evals/parity/` — **claim 3**. For every feature and a population of entities, the
      online value equals the offline value computed at the same instant. Deliberately
      include a case where a naive implementation would leak a future value, and assert the
      harness catches it.
- [ ] `evals/freshness/` — **claim 4**. A feature past its budget is never served; the
      decision falls back; the fallback marker survives to the decision record.
- [ ] `contracts/decisions/` — the three decision contracts, including fallback rules and
      actuation policy. **Load-time enforcement of claim 7** goes in here now, even though
      its eval arrives in Phase 3: a contract with `effect: significant_on_person` and
      `actuation: automatic` must fail to load.
- [ ] `src/watermark/decisions/` — the decision engine and the deterministic fallback rules.
      The curtailment fallback must be computable with no model and no fresh features.
- [ ] `infra/lakehouse/` and the Feature Store definitions in `infra/ml/`. Glue Data Quality
      rules as a gate on the offline side.

**Done when:** claims 3 and 4 pass offline, a stale feature demonstrably cannot reach a
decision, and every decision record states whether it came from a model or a fallback.

---

## Phase 3 — The model lifecycle

*Unlocks claims 5 and 7.*

- [ ] `src/watermark/models/` — training for both models from the offline store, as-of a
      pinned snapshot. Reproducible: the same snapshot yields the same model metrics.
- [ ] Bias analysis on the anomaly path, with the proxy-discrimination risk from
      `docs/SCENARIO.md` as the thing actually measured — not a generic fairness metric
      chosen because it is easy to compute. Write down what was found, including if it is
      uncomfortable.
- [ ] The **promotion gate**: performance thresholds, bias thresholds, a model card generated
      from the training run, and a named human approver. Nothing self-approves (doctrine 5).
- [ ] `evals/promotion/` — **claim 5**. A model that fails each threshold in turn is refused,
      and refused *for that threshold*.
- [ ] `src/watermark/decisions/` oversight queue: the anomaly path's inspector flow, the
      recorded accept/reject, and the rejection as a training signal.
- [ ] `evals/oversight/` — **claim 7**. Attempt to actuate a significant decision without a
      recorded human decision, through every path that exists. All must fail.
- [ ] SageMaker Pipelines, Model Registry, Clarify, Model Monitor, Model Cards, endpoint with
      shadow → canary → auto-rollback on drift or p99 SLO breach. In `infra/ml/`, validated.
- [ ] A rollback rolls back *both* the model and the feature snapshot it was trained against.

**Done when:** claims 5 and 7 pass offline, the bias finding is written down, and a rollback
is demonstrated end to end in the offline harness.

---

## Phase 4 — Governance, erasure, recovery, and the live capture

*Unlocks claim 6 and closes the project.*

- [ ] `src/watermark/policy/` — Lake Formation tag policy authored in the repository and
      evaluated **offline**, the way Attestor evaluates Cedar. The deployed grants and the
      offline evaluator must read the same bytes.
- [ ] A Lake Formation access suite: for each principal and each tag combination, the
      expected reachable set — and the paths that must be closed.
- [ ] `src/watermark/erasure/` — the KMS key hierarchy, the Step Functions orchestration
      across Iceberg, offline store, online store, training sets and model artefacts, and the
      **completeness proof**. The system refuses to report "erased" unless every leg confirms.
- [ ] `evals/erasure/` — **claim 6**. Erase a subject, then attempt to reach them through
      every store. Include a deliberately incomplete run and assert the system refuses to
      certify it.
- [ ] Recovery drill: kill the job mid-window, restore from savepoint, assert no double
      counting. Declared RPO/RTO, tested rather than written.
- [ ] Generated technical documentation (Annex IV shape) + the DPIA for the anomaly path,
      CI-failing on drift from the code.
- [ ] Cost telemetry: cost per decision, cost per meter.
- [ ] `scripts/preflight.py` — every claim, every consistency invariant, `terraform validate`
      against real provider schemas, checkov at zero findings. One command, all checks.
- [ ] `README.md` with a scoreboard, in the style of Attestor's: numbers that are the output
      of a command in the repository, not a summary of one.
- [ ] ~~**The live capture.**~~ **Out of scope — nothing is ever applied to AWS.** The estate
      is built, validated against real provider schemas, scanned, and left unapplied. Decided
      2026-08-09: a live capture costs real money and adds nothing to any of the seven claims,
      every one of which is provable offline by construction. The replacement deliverable is
      `make preflight` green, `terraform validate` against real provider schemas, and checkov
      at zero findings — the same posture as `../attestor`: **ready to deploy, not deployed.**
      Consequences that are easy to get wrong: no screenshot, no wall-clock time and no euro
      figure may be published as if it were measured, and the €100 target is a design
      constraint rather than a result.

**Done when:** `make preflight` is green, every layer validates and scans clean, and the
README's scoreboard is reproducible by a stranger with no AWS account.

---

## After Phase 4 — not part of building the system

Handled separately, per `docs/PORTFOLIO-CONTEXT.md`: the site card, the CV entries, the
video walkthrough, the long-form article, and the second worked example of the Readiness
Framework. Do not start these before Phase 4 is done — a system shaped to look good in a
portfolio card is a worse system.
