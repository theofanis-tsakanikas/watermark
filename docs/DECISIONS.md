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

**15 · ~~Nothing is ever applied to AWS.~~** *Superseded 2026-08-11 by decision 17. Kept in
full, because the argument was wrong in a way worth being able to read.*

> The estate is built, formatted, validated against real provider schemas and scanned to zero
> findings — and left unapplied. Not once, not briefly, not from a laptop.
>
> The reasoning is that the capture was never load-bearing. All seven claims are provable
> offline **by construction** — that is the whole design, and it is why `src/watermark/core/`
> may not import a cloud SDK. A live run would therefore have produced a screenshot, not a
> proof, and it would have cost real money to produce something the repository already
> demonstrates for free. The posture is Attestor's: **ready to deploy, not deployed.**

**16 · The repository is English. The conversation is Greek.**

**17 · The estate is deployed, driven and destroyed. Decision 15 was wrong.**
*Decided 2026-08-11, after doing it.*

Every layer has been applied to a real AWS account through the gated workflow, the scenario has
been driven through it, and the whole estate has been destroyed again. `infra/bootstrap/` was
applied from a laptop on 2026-08-10, as its own design always intended.

**Why 15 was wrong, precisely.** Its argument was that the claims are provable offline, so a
live run adds nothing. The first half is true and is still the design. The second half does not
follow, and confusing the two is the error worth naming: *proving the logic offline says nothing
about whether the estate that would carry it can exist.* Those are different propositions, and
only one of them had been checked.

The run found things `terraform validate` and checkov cannot, because none of them is a schema
error. The full list is in the commit history; four stand for the rest:

- **SageMaker Clarify is in maintenance mode and unavailable to new customers.** The pipeline
  step could not pull its image. ADR-0006 is amended rather than quietly kept, and the honest
  claim shrank: this project does not run Clarify, it reimplements the metrics that mattered.
- **PyFlink cannot emit a custom watermark.** The Python API has no `for_generator`; the
  strategy the job was written around does not exist in the language the job is written in.
- **Writing Iceberg from PyFlink did not work**, through four attempted classpath layers. The
  design moved to a landing prefix plus a Glue `MERGE INTO` — which is a better design, arrived
  at by failing.
- **The publisher ran dry and the workflow went green.** Zero records reached Kinesis and every
  step reported success, because nothing asserted that anything had moved. A green run that did
  nothing is exactly the failure this project exists to argue against, and it was in our own CI.

None of that is discoverable offline, and none of it is a screenshot.

**What is claimed, and what is not.** Claims 1, 5 and 6 were exercised live — every published
window carried its watermark status, the model registered `PendingManualApproval`, and the
erasure state machine **refused to certify**, which was the correct answer. Claim 2 was not:
`land_to_silver` was blocked on a missing IAM read, so settlement had nothing to total. The
endpoint and Model Monitor were not exercised either, because both need a model a human has
approved and no human had. **Those are gaps, not results, and they are listed as gaps in
`README.md`.**

**What this permits, and its limits.** Euro figures and wall-clock times may now be stated —
*when they are measured, labelled as measured, and given their error bars.* The tagged spend for
the whole exercise was **USD 12.35**; the true figure is higher, because the cost allocation tag
takes up to 24 hours to activate and the early hours of the estate are therefore untagged. Both
halves of that sentence must travel together. A measurement quoted without the reason it
undercounts is worse than the design constraint it replaced.

The **under €100** target in `CLAUDE.md` remains a design constraint. It is now also survivable
evidence, and it was met.

`docs/DAY-ONE.md` no longer says the list is undone. It says which items were done, when, and
what each one taught.

## Deliberately deferred

- The video walkthrough — Phase 4. (The live capture is no longer deferred and no longer out of
  scope; it has been done. See 17.)
- The second worked example (the Readiness Framework scored against Watermark) — after the
  system exists, never as a design target. A build shaped to score well on its author's own
  framework proves nothing about either.
