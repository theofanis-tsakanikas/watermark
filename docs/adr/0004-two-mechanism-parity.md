# ADR-0004 — Train/serve parity is proved between two mechanisms, not one

**Status:** accepted · **Date:** 2026-08-09 · **Documentation verified:** 2026-08-09

## Context

Claim 3 says every feature served online equals the offline value computed for the same entity
at the same instant, and that CI fails on any divergence. It is the claim nothing else in this
portfolio proves, and it is the easiest of the seven to fake.

There are two ways to fake it, and both report green.

**The shared function.** One `compute_feature()` is called by the online path and by the
offline path, and the harness asserts the two results are equal. They are equal because they
are the same call. The test proves that `x == x`, runs in eleven milliseconds, and is worth
nothing. This is the default outcome of a sensible instinct — *don't write the feature twice* —
and it is why the design has to be settled before the feature code exists rather than after.

**The shared write.** Subtler, and specific to SageMaker Feature Store. A `PutRecord` on a
feature group with both stores enabled writes the online record and populates the offline
store from the same call. Comparing the online store against the Feature Store's *offline
store* therefore compares a value with a copy of itself made by AWS. It exercises the Feature
Store's plumbing — which is not broken and is not what claim 3 is about.

Writing this ADR in Phase 0 rather than Phase 2 is deliberate: deciding it after the feature
code exists means rewriting the feature code.

## Decision

**One contract. Two independent executions. Compared bitemporally, with no tolerance.**

### The two mechanisms

| | Online | Offline |
|---|---|---|
| Definition | the feature contract | the same feature contract |
| Compiler | contract → streaming aggregator configuration | contract → SQL |
| Engine | Flink, incrementally, on the event stream | Athena, as a single as-of query |
| Data path | Kinesis → Managed Flink → `PutRecord` | Iceberg tables of raw readings |
| Read by | `GetRecord`, at decision time | the parity harness, and Phase 3 training |

Only the contract is shared. Two compilers, two engines, two data paths, two type systems.
A divergence is therefore possible — and being possible is exactly what makes agreement
evidence.

This is not "two implementations of one feature", which is the bug Phase 2 exists to prevent.
That bug is two *definitions*: a feature that means one thing in training and another in
serving. Here there is one definition and two executions of it, which is the only arrangement
in which the executions can be checked against each other at all.

### The offline side is the raw lakehouse, not the Feature Store's offline store

The as-of query reads the **Iceberg tables of raw readings** and recomputes the feature from
first principles. It does not read the Feature Store offline store, for the reason above.

The Feature Store offline store still has its job — it is what Phase 3 trains from, and it is
what makes the trained model's inputs the same objects the serving path produced. A separate,
cheaper check compares it against the online store. That check is a **smoke test on AWS's
plumbing, not claim 3**, and the README will not present it as one.

### Parity is bitemporal, because the two stores handle late data differently

From the SageMaker documentation, verified 2026-08-09:

> The online store only contains the record corresponding to the latest event time, whereas
> the offline store contains all historic records.

and, on ingesting a record whose event time is earlier than the stored one: the online store
keeps the original, the offline store keeps both.

Late data is the central pathology of this system. A reading that arrives three days late
changes what the offline recomputation says the feature was at instant *T*. It cannot change
the online record that was already stored under a later event time, and it should not — the
decision was taken with what had arrived.

So a naive parity check *"online value now == offline value as of T"* fails on every late
arrival, correctly reports a divergence, and is measuring the wrong thing. The comparison is:

> the value **served** at instant `T_serve`, for event time `T_event`
> **equals**
> the value the offline query computes for `T_event` **using only rows whose ingestion time is
> at or before `T_serve`**.

Two time axes: event time decides *what the feature is about*, ingestion time decides *what
was knowable when the decision was taken*. Collapsing them is the same error as taking a
decision on a window that has not closed, one layer up.

The Feature Store offline store records `write_time` and `api_invocation_time` — both are
reserved feature names for exactly this reason — and the raw Iceberg tables carry an ingestion
timestamp minted at the edge. The as-of query binds both.

### No tolerance. Ever.

Doctrine rule 7 says the parity door has no key. A floating-point tolerance in the comparison
is a key: it starts at 1e-9 to make a test pass, and it is 0.01 by the time anybody looks
again.

Feature Store supports three value types — String, Fractional (IEEE-754 double) and Integral
(64-bit signed) — and nothing else. An energy reading held as `decimal(18,3)` in Iceberg and
as a double in the online store will differ in the last bits, and no amount of care in the
aggregation prevents it. The answer is representation, not tolerance:

**A feature whose offline type cannot be represented exactly in one of the three Feature Store
types must declare a scaled-integer representation in its contract.** Watt-hours as `Integral`,
not kilowatt-hours as `Fractional`. The contract states the scale; both compilers read it; the
comparison is integer equality. A feature that cannot be represented exactly and does not
declare a scale **fails to load**, in the same way and for the same reason as a feature with
no freshness budget.

### The independence is enforced, not intended

`scripts/check_parity_paths_are_independent.py`, arriving with the feature code in Phase 2:
the offline resolver and the online materialiser may not share any module except the contract
loader and `watermark.core.time`. The check reads the import graph.

It ships with its mutation in the same commit: make the offline resolver call the online
aggregator, and require the named gate to refuse it. Without that gate, "two mechanisms" is a
sentence in a document, and the first refactor that notices the duplication will delete the
claim.

## The cases the harness plants

Each is a way parity can be false while looking true. `evals/parity/` carries all of them, and
a case that has never failed on a deliberately broken resolver is not carrying its weight.

1. **Future leakage.** The offline store keeps every historical record, so a resolver that
   takes "the latest row for this entity" reads a value from after `T_event`. This is the
   planted case `CLAUDE.md` and `PLAN.md` both name, and it is the classic route to a model
   that scores beautifully in evaluation and is useless in production.
2. **Late arrival.** A reading ingested after `T_serve` changes the offline recomputation and
   must not enter the comparison. A harness that binds only event time fails this case; a
   harness that binds both passes it, and the difference between the two is claim 3 meaning
   something.
3. **Duplicate event time.** `(record identifier, event time)` uniquely identifies a record, so
   two readings for one meter at one instant collide in the online store. Deduplication must
   happen before materialisation, and the harness proves it did.
4. **Representation.** A feature declared `Fractional` whose offline value is a decimal — the
   contract loader must refuse it before the harness ever runs.
5. **Event-time rendering.** Feature Store accepts a string event time in exactly two shapes:
   `yyyy-MM-dd'T'HH:mm:ssZ` and `yyyy-MM-dd'T'HH:mm:ss.SSSSSSSSSZ`. This repository's canonical
   instant renders three decimal places, which is neither. The adapter widens to nine; the
   core does not change, because three decimal places is what Flink carries. A test asserts the
   widening, because the failure is a rejected write at ingestion time or, worse, a silently
   different instant.
6. **Freshness is not a parity failure.** An entity whose online record is past its freshness
   budget is a claim 4 case: it must not be served at all. The parity harness asserts over
   entities within budget and asserts *separately* that the stale ones were refused. Conflating
   them makes both claims mushy.

## Consequences

- The feature contract must carry enough to compile SQL *and* to configure a streaming
  aggregator: entity key, event-time field, window, grain, aggregation, filter, output type
  and scale, freshness budget, purpose, owner. It is a bigger contract than a feature store
  usually asks for, and that is the price of the claim.
- Two compilers is more code than one function, and it is the deliverable rather than the cost.
- Parity is asserted over a *population* of entities and instants, not one, and the harness
  reports how many of each — a claim 3 row on the scoreboard reading "1/1" would be a worse
  result than no row.
- Where a genuine, documented divergence exists between the two stores, it is written into the
  comparison rule here, in advance, with the reason. A divergence discovered later is a bug
  until this ADR is amended to say otherwise. That order matters: it is what stops the rule
  being relaxed to fit the failure.

## Sources consulted, 2026-08-09

- *Feature Store concepts*, Amazon SageMaker Developer Guide — record identity as
  (record identifier, event time); online store keeps only the latest event time; offline store
  keeps all records; ingestion of an earlier event time.
- *Quotas, naming rules and data types*, Amazon SageMaker Developer Guide — the three feature
  types; the two accepted ISO-8601 event-time patterns and the epoch-seconds alternative;
  String-only event time for Iceberg-format feature groups; `is_deleted`, `write_time` and
  `api_invocation_time` as reserved names.
