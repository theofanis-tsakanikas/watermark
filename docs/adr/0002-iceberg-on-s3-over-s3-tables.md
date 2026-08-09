# ADR-0002 — Iceberg on S3 with the Glue Data Catalog, not S3 Tables

**Status:** accepted · **Date:** 2026-08-09 · **Documentation verified:** 2026-08-09

## Context

`docs/DECISIONS.md` decision 9 left this open deliberately: *"Iceberg on S3 + Glue Catalog is
the default; S3 Tables is a decision to make with the current docs open. Do not choose it
because it is newer."*

S3 Tables is managed Apache Iceberg: table buckets, an Iceberg REST endpoint, integration with
the Glue Data Catalog and Lake Formation, and — the actual product — maintenance that AWS
runs for you. Compaction, snapshot expiry and orphan-file removal are the three jobs every
self-managed Iceberg lakehouse has to run, get wrong, and eventually pay for. Having them
handled is not a small thing, and the workload here is exactly the one that suffers without
them: 250,000 meters uploading in a burst after each 15-minute boundary is a small-file
generator.

Both options can be reached from Flink, both can be queried by Athena, both support Lake
Formation fine-grained access control, and both support SSE-KMS with a customer-managed key.
The choice is not about capability in the ordinary sense. It is about which of the two lets
this repository *prove* the things it claims.

## Decision

**Iceberg on S3, catalogued in Glue, with maintenance we run ourselves.**

Three reasons, in order of weight. Each is a fact checked against the current documentation on
the date above, not a preference.

### 1. S3 Tables snapshot management cannot honour a pinned snapshot

Claim 2 and the settlement restatement path both rest on the same mechanism: a published total
is bound to the exact table state it was computed from, and that state stays reachable, so the
number can be recomputed and the restatement can state the delta against it. In Iceberg the
tool for *"this snapshot must survive because a number was published from it"* is a **tag**
with its own retention.

From the S3 Tables maintenance documentation:

> Snapshot management does not support retention values you configure as Iceberg table
> properties in the `metadata.json` file or through an `ALTER TABLE SET TBLPROPERTIES` SQL
> command, including branch or tag-based retention. Snapshot management is disabled when you
> configure a branch or tag-based retention policy […] In these cases S3 will not expire or
> remove snapshots and you will need to manually delete snapshots or remove the properties
> from your Iceberg table to avoid storage charges.

So on S3 Tables the choice is: a single global age policy with no per-snapshot pinning, or tag
the snapshots you need and lose managed snapshot maintenance entirely. The second is
self-managed Iceberg with extra steps and a bill.

The defaults sharpen it. `maximumSnapshotAge` defaults to **120 hours** and `minimumSnapshots`
to **1**. A batch that arrives three days late — the central pathology in `docs/SCENARIO.md`,
and the reason claims 1 and 2 exist — restates a total whose snapshot is, by default, about to
expire. The number would still be *computable*; it would not be *reproducible from the state
it was computed from*, which is the whole of claim 2.

### 2. Erasure needs maintenance we can schedule and confirm

Claim 6 does not deliver a deletion mechanism. It delivers a **completeness proof**: the
system refuses to certify erasure unless every leg confirms. A leg that reads *"the data files
containing this subject will be removed by a maintenance service on a schedule we neither set
nor observe"* is not a confirmation. It is a promise about somebody else's cron.

Compaction on S3 Tables *"occurs on an automated schedule"*, and unreferenced file removal is
a table-bucket-level job with a minimum retention of one day. Under self-managed Iceberg the
same three routines — `rewriteDataFiles`, `expireSnapshots`, `deleteOrphanFiles` — are jobs
this repository defines, invokes from the erasure Step Function, and waits on. The
certificate can then state which run removed which files, which is what makes it a proof
instead of an assertion.

This does not make erasure easy. It makes the residual window *ours to state*, which is the
whole posture of claim 6 (see `docs/DECISIONS.md` decision 11).

### 3. The mechanism is the deliverable

The project exists to demonstrate that these properties are enforced rather than described.
Handing table maintenance to a managed service is the right engineering decision in most
production estates and removes precisely the surface this repository is meant to show working.
That is not a reason on its own — a portfolio is not a reason to build something worse — but
where the first two arguments already point the same way, it settles the tie.

## What we are giving up, stated plainly

- **Small-file management becomes our problem**, and the burst profile makes it a real one.
  Compaction is a scheduled job we write, it costs money to run, and if we get its cadence
  wrong the Athena queries behind settlement get slower over the capture window.
- **Operational work AWS would otherwise do.** Three maintenance routines, their failure
  modes, and their observability.
- **S3 Tables gets better at a rate we do not control and would have benefited from.** It has
  shipped capability quickly since launch.

## When this decision flips

Written down now so the reversal is a checkable event and not a mood:

1. S3 Tables snapshot management honours Iceberg branch and tag retention — that is the single
   sentence in the documentation that makes reason 1 disappear.
2. Compaction and unreferenced file removal become invocable on demand with an observable
   completion status per table, so an erasure orchestration can wait on them.

If both land, reason 3 alone should not keep us here, and this ADR is superseded rather than
argued with.

## Consequences

- `infra/lakehouse/` provisions plain S3 buckets, Glue Catalog databases and tables, and Athena
  workgroups. No `aws_s3tables_*` resources.
- Maintenance is explicit: a compaction job, a snapshot-expiry job, and an orphan-file job,
  each with a stated cadence, each invokable from a Step Function.
- **Snapshot retention is a policy in the repository, not a default.** Any snapshot a published
  number was computed from is tagged, and the expiry job refuses to remove a tagged snapshot.
  That refusal is a gate, and it will carry a `gate-proof` mutation that unpins a published
  snapshot and requires the refusal.
- Lake Formation tag-based access control applies to Glue-catalogued Iceberg tables, which is
  what `src/watermark/policy/` will evaluate offline in Phase 4.

## Sources consulted, 2026-08-09

- *S3 Tables maintenance* and *Considerations and limitations for maintenance jobs*, Amazon S3
  User Guide — the defaults table (`targetFileSizeMB` 512 MB, `minimumSnapshots` 1,
  `maximumSnapshotAge` 120 h, `unreferencedDays` 3, `nonCurrentDays` 10) and the branch/tag
  retention limitation quoted above.
- *Amazon S3 Tables integration with AWS Glue Data Catalog and AWS Lake Formation*, Lake
  Formation Developer Guide — confirms fine-grained access control is available on S3 Tables,
  which is why it is not one of the reasons above.
- *Specifying server-side encryption with AWS KMS keys (SSE-KMS) in table buckets* — confirms
  customer-managed keys are supported on S3 Tables, which is likewise why encryption is not
  one of the reasons above.
