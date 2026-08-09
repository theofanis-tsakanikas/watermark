# Decisions locked before the first line of code

These were settled in the planning conversation. They are not open for re-litigation at the
start of a session; they are open for revision only with a reason, and a revision becomes an
ADR under `docs/adr/`.

## Scope

**1 · All three decision paths stay.** Curtailment, meter anomaly, settlement. The settlement
path looks like the least impressive and is not: it is the only one that produces the
restatement claim, and restatement is the thing real data platforms get wrong.

**2 · The name is `Watermark`.** It is the Flink term and the thesis. The tagline is *"no
decision on a window that has not closed."*

**3 · AWS only.** No Azure, no GCP, no Databricks. Two projects in this portfolio already
demonstrate multi-cloud; a third would say nothing new. The goal here is AWS depth.

**4 · No RAG, no agent, no LLM as a subject.** Three projects already cover generative
systems. If a language model appears at all, it appears only as an *explainer* over numbers
that deterministic code produced — and even that is optional, deferred to the end, and cut
without regret if it costs time.

**5 · Redshift is out of scope.** It adds no claim here. If Redshift coverage is wanted for
the CV, it is a separate small repository (zero-ETL + dbt-redshift), not a bolt-on.

**6 · Multi-tenancy is out of scope.** Attestor proved tenant isolation properly. Repeating
it here would dilute both.

## Technology

**7 · Flink, not Spark Structured Streaming.** Event-time semantics, watermarks, allowed
lateness and side outputs for late data are first-class in Flink and awkward in Spark. Two
projects already use Spark streaming. The claims of this project are precisely the ones
Flink's model is built for.

**8 · PyFlink, with the real logic outside Flink.** Python is the portfolio's primary
language and Managed Service for Apache Flink supports Python applications. The decisive
argument is testability: with windowing, deduplication, point-in-time joins and lateness
handling implemented as pure functions in `src/watermark/core/`, claims 1–4 are provable in
a plain pytest run. The PyFlink job is an adapter. If PyFlink's operator surface turns out
to force logic into the framework, the answer is to move that logic *out*, not to move the
tests in.

**9 · Iceberg on S3 + Glue Catalog is the default; S3 Tables is a decision to make with the
current docs open.** S3 Tables (managed Iceberg) is the more modern answer and is worth
using if its region and Lake Formation integration support what claims 2, 4 and 6 need.
Verify against current documentation at the start of Phase 1 and write the ADR either way.
Do not choose it because it is newer.

**10 · SageMaker Feature Store for both online and offline.** It is the mechanism that makes
claim 3 (train/serve parity) a platform property rather than a convention, and it is the
single biggest gap in the portfolio's AWS coverage.

**11 · Erasure is crypto-shredding, per data subject — with a declared boundary.** A KMS key
hierarchy with a key per subject; erasure destroys the key and then *proves* the subject is
unreachable in every store. Physical row deletion in Iceberg is the fallback for anything the
key hierarchy cannot cover, and the completeness proof — not the deletion mechanism — is the
deliverable.

**Crypto-shredding does not reach a trained model's weights.** The subject's contribution is
statistically inside the artefact and no key protects it; claiming otherwise would be exactly
the kind of overclaim this portfolio exists to argue against. That leg is therefore satisfied
by quarantining the affected model and retraining from the shredded corpus on a stated
schedule, with the **residual window declared on the certificate**. Machine unlearning is not
claimed and is not attempted. The value of claim 6 is the refusal to certify an incomplete
run, not an assertion of perfection.

**12 · Grafana / Prometheus via the managed services, ADOT for traces.** Consistent with
Fleet Risk; the lineage id travels from the event to the decision and appears in the trace.

## Method

**13 · Seven claims, each provable offline.** The list is in `CLAUDE.md` and does not grow
casually. A new claim must be as sharply falsifiable as the existing seven.

**14 · `gate-proof` from the beginning, not at the end.** Every gate ships with the mutation
that breaks it, in the same commit. Attestor's rules apply: green first; a non-zero exit is
not evidence; a moved target is STALE, not passed.

**15 · Live capture once, at the end, and destroyed.** The estate stands up in one gated
workflow, drives the scenario, captures evidence, and is destroyed. Target under €100.

**16 · The repository is English. The conversation is Greek.**

## Deliberately deferred

- Live capture, screenshots and the video walkthrough — Phase 4.
- The site and CV integration — see `docs/PORTFOLIO-CONTEXT.md`; it is real work and it is
  not part of building the system.
- The second worked example (the Readiness Framework scored against Watermark) — after the
  system exists, never as a design target. A build shaped to score well on its author's own
  framework proves nothing about either.
