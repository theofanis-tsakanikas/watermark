# AWS constraints that shape the design

**Verified against current AWS documentation on 2026-08-09.** Every figure below was read in
the service documentation on that date. Sources are listed per section. Nothing here is
recalled from memory, and a number without a source does not belong in this file.

---

## Why this file exists, and why the Terraform does not yet

The Terraform for each layer is written in the phase that layer belongs to, because the shape
of the infrastructure is decided by the code that runs on it. The Flink application's
parallelism follows from how the operators partition; the Kinesis shard model follows from the
partition key; the Feature Store schema follows from the feature contracts; the Iceberg
partitioning follows from how the settlement queries read. Writing that HCL now would mean
inventing the answers and then bending the code to match them.

What *does* have to be known early is the set of facts the services impose regardless of what
we write — the ones that make a design impossible rather than merely different. Those are
here. They are what de-risks the infrastructure; the HCL is what expresses it.

Three of the facts below already changed a decision:

- Kinesis on-demand scales over about fifteen minutes and this workload's burst lasts about
  three. → **provisioned mode**, sized for the burst.
- The Feature Store's accepted event-time strings are second- or nanosecond-precision, and this
  repository's canonical instant is millisecond-precision. → the adapter widens; the core does
  not change.
- The Feature Store has three value types and none of them is a decimal. → features declare a
  scaled-integer representation rather than a comparison tolerance (ADR-0004).

---

## Amazon Kinesis Data Streams

**The facts.**

- On-demand mode accommodates up to **double the peak write throughput observed in the
  previous 30 days**, and scales as new peaks are set.
- **Write throttling occurs if traffic increases to more than double the previous peak within
  a 15-minute window.** When per-shard traffic exceeds 500 KB/s, Kinesis splits the shard
  *within 15 minutes*.
- A single partition key is still bounded by one shard's limits: **1 MB/s and 1,000 records per
  second**. On-demand splits shards evenly on traffic; it does **not** detect and isolate a hot
  hash key. The documentation's own recommendation for highly uneven keys is provisioned mode
  with granular splits.
- Provisioned sizing:
  `number_of_shards = ceiling(max(incoming_write_bandwidth_KiB / 1024, outgoing_read_bandwidth_KiB / 2048))`
- On-demand Advantage allows pre-warming write capacity, but commits the account to at least
  25 MiB/s of ingest and 25 MiB/s of retrieval across the Region.
- A stream can switch between on-demand and provisioned **twice per 24 hours**.

**What it means here.** `docs/SCENARIO.md` describes ~900 events/s typical and ~4,000 events/s
peak, and the peak is not random: most meters upload within the same few minutes after each
15-minute boundary, and a firmware cohort that retries can double the spike. That is a
periodic burst with a duty cycle of roughly three minutes in fifteen — shorter than the time
on-demand takes to react. On-demand would throttle the front of every burst and then scale up
in time for the quiet part.

So: **provisioned mode, sized for the burst, for the bounded live capture.** On-demand
Advantage's warm throughput would be the production answer and its 25 MiB/s commitment is far
outside the €100 target for a capture that runs for an hour.

**Partition key.** `meter_id` — 250,000 values, evenly distributed by hash. Partitioning by
`substation_id` (400 values) would concentrate a large meter population behind one key and run
into the 1 MB/s per-key ceiling that on-demand explicitly does not fix. The consequence is that
per-substation aggregation is a keyed operation *inside* Flink rather than a property of the
stream, which is where it belongs anyway.

**What is not verified.** Actual burst shape against a real AMI head-end. The generator in
`data/` makes the burst up, so the shard count is sized against a synthetic profile and the
live capture will say whether the profile was fair.

*Source: “Choose the right mode to stream in”, Amazon Kinesis Data Streams Developer Guide.*

---

## Amazon Managed Service for Apache Flink

**The facts.**

- A **KPU is 1 vCPU and 4 GB of memory**, plus 50 GB of running application storage.
- `Allocated KPUs = Parallelism / ParallelismPerKPU`. `Parallelism` defaults to 1 with a
  default maximum of 256; `ParallelismPerKPU` defaults to 1 with a maximum of 8. The default
  KPU limit per application is 64. **An additional KPU is charged for orchestration.**
- Autoscaling is on by default (`AutoScalingEnabled`); it adds capacity quickly on a spike and
  removes it gradually.
- `maxParallelism` is the ceiling for scaling *while retaining state*. It is the minimum
  `maxParallelism` across all operators, defaults to 128 for an application started at
  parallelism ≤ 128, and **changing it means the application cannot restart from a snapshot
  taken with the old value** — it can only restart without state.
- Python applications are packaged as a **zip** uploaded to S3, with entry points declared in
  the `kinesis.analytics.flink.run.options` property group: `python`, `jarfile`, `pyFiles`,
  `pyArchives`. Connector JARs are packaged with the application.
- Savepoints are called **snapshots**; they are taken automatically when an application is
  updated and can be triggered by the user.
- Runtime: Flink **2.3** is supported as of 2026-07, Flink **2.2** as of 2026-03. Flink 2.2
  requires Python 3.9+ and **defaults to Python 3.12**; Python 3.8 is not supported. In-place
  version upgrades are available.

**What it means here.**

- Python 3.12 is this repository's floor and Flink 2.2's default. That is not a coincidence to
  rely on silently: the local `apache-flink` version and the deployed `runtime_environment`
  are compared by a script (ADR-0003), because an equivalence test against a different Flink
  than the one running proves equivalence with something nobody uses.
- **`maxParallelism` is set explicitly, in the first version of the application.** Leaving it to
  default and discovering later that the recovery drill cannot restore state across a rescale
  would be a Phase 4 failure caused by a Phase 1 omission. It is also the one setting on this
  list that cannot be corrected without losing state.
- The recovery drill in Phase 4 — kill the job mid-window, restore from a snapshot, assert no
  double counting — uses a user-triggered snapshot, which is why it is testable at all.
- Cost: KPUs are one of the three expensive things in `CLAUDE.md`, and the orchestration KPU
  means the floor is *n+1*, not *n*. Autoscaling stays enabled for the capture and the KPU
  ceiling is set low enough that a runaway cannot outrun the budget guard.

**What is not verified.** Whether PyFlink on the managed runtime imposes any restriction that
forces a semantic decision into the framework. ADR-0003 states the answer if it does — move the
decision out, never the tests in — but the question is settled in Phase 1 with a job that runs,
not here.

*Sources: “Implement application scaling in Managed Service for Apache Flink”; “Create your
Managed Service for Apache Flink Python application”; AWS What's New, Flink 2.2 (2026-03) and
Flink 2.3 (2026-07).*

---

## Amazon SageMaker Feature Store

**The facts.**

- A record is uniquely identified by **(record identifier, event time)**.
- **The online store keeps only the record with the latest event time.** Ingesting a record
  with an earlier event time leaves the stored record unchanged. **The offline store keeps all
  historical records.**
- Feature value types are **String, Fractional (IEEE-754 double) and Integral (64-bit signed)**.
  There is no decimal, boolean or timestamp type.
- Event time has nanosecond precision and may be String or Fractional. A **String** event time
  is accepted in UTC ISO-8601 matching exactly `yyyy-MM-dd'T'HH:mm:ssZ` or
  `yyyy-MM-dd'T'HH:mm:ss.SSSSSSSSSZ`. A **Fractional** event time is seconds since the Unix
  epoch. **For feature groups in Iceberg table format, the event time must be String.**
- Reserved feature names: `is_deleted`, `write_time`, `api_invocation_time`.
- Limits: 350 KB maximum record size; 2 KB maximum record identifier; 2,500 feature definitions
  per feature group; 500 WRU/s and 2,400 RRU/s **per record identifier**; a soft limit of 100
  feature groups per account.

**What it means here.**

- **The millisecond gap is real.** This repository's canonical instant renders three decimal
  places (`2026-03-14T09:15:00.070Z`), which matches neither accepted pattern. The Feature Store
  adapter widens to nine (`...070000000Z`). The core is not changed to suit it: three decimal
  places is what Flink carries, and a core that rendered nanoseconds would be claiming a
  precision the runtime does not have. A test asserts the widening, because the failure mode is
  a rejected write — or an accepted one at a different instant.
- **No decimal type is the reason ADR-0004 forbids a comparison tolerance.** kWh as a double
  compared against `decimal(18,3)` in Iceberg differs in the last bits by construction. Features
  declare a scale and travel as `Integral`.
- **The late-data asymmetry between the two stores is a design input, not a defect.** It is why
  claim 3's comparison is bitemporal: event time decides what the feature is about, ingestion
  time decides what was knowable when it was served. See ADR-0004.
- 500 WRU/s per record identifier is far above anything this workload does per meter, and the
  100-feature-group soft limit is far above what the contracts will declare. Neither binds.
- `write_time` being reserved is the mechanism the bitemporal parity query binds on.

**What is not verified.** The offline store's ingestion lag — how long after a `PutRecord` a
record is queryable offline. It is documented as asynchronous and buffered; no figure was read.
Nothing depends on it, because the parity comparison is as-of ingestion time rather than
wall-clock time, and that independence is deliberate: a check whose correctness depends on an
unpublished latency is a flaky check waiting for a busy afternoon.

*Sources: “Feature Store concepts” and “Quotas, naming rules and data types”, Amazon SageMaker
Developer Guide.*

---

## Apache Iceberg on S3 — maintenance

ADR-0002 chose Iceberg on S3 with the Glue Data Catalog over S3 Tables, and the reasoning
there rests on these facts. They are recorded here too, because after that decision they stop
being an argument and become work this project has to do.

**The facts.** The three maintenance routines are `rewriteDataFiles` (compaction),
`expireSnapshots` and `deleteOrphanFiles`. For reference, S3 Tables' managed equivalents
default to a 512 MB compaction target, a minimum of 1 retained snapshot, a **120-hour** maximum
snapshot age, 3 days before an unreferenced file is removed and 10 days for a non-current one.
S3 Tables' snapshot management **does not support Iceberg branch- or tag-based retention** and
switches itself off if one is configured.

**What it means here.** All three routines are ours to schedule, observe and invoke:

- **Snapshot expiry must refuse to remove a tagged snapshot.** A published settlement total is
  bound to the snapshot it was computed from; that snapshot is tagged, and the tag is what
  makes claim 2 and the restatement path work three days later. This is a gate, and it will
  ship with the mutation that unpins a published snapshot.
- **Compaction is invocable from the erasure orchestration**, because claim 6's completeness
  proof has to confirm that the files holding a subject were rewritten — not predict that they
  will be.
- **Compaction cadence is a real cost.** 250,000 meters in a 15-minute burst is a small-file
  generator, and this is the operational work ADR-0002 accepted in exchange for control.

*Source: “S3 Tables maintenance” and “Considerations and limitations for maintenance jobs”,
Amazon S3 User Guide — read for their defaults table, which documents the Iceberg routines the
self-managed jobs call directly.*

---

## AWS Lake Formation

**The facts.** Lake Formation provides coarse-grained (database and table) and fine-grained
(column- and row-level) access control over Glue Data Catalog tables, and — as of 2026 — over
S3 Tables through the Glue Data Catalog integration.

**What it means here.** Fine-grained control is available under either table format, which is
why it appears nowhere in ADR-0002's reasoning. Phase 4 authors the tag policy in the
repository and evaluates it offline, the way Attestor evaluates Cedar; the deployed grants and
the offline evaluator must read the same bytes.

**What is not verified.** The exact Regional availability of the S3 Tables integration, and
the precise semantics of tag inheritance across databases and tables. Neither is load-bearing
today; both are verified in Phase 4, before the policy suite is written.

*Sources: “Amazon S3 Tables integration with AWS Glue Data Catalog and AWS Lake Formation” and
“What is AWS Lake Formation?”, AWS Lake Formation Developer Guide.*

---

## Re-verification

Every section above is dated. AWS changes; a constraint recorded in August 2026 and relied on
in December 2026 is a memory, not a fact. When a phase begins, the sections it depends on are
re-read and re-dated, and a change that invalidates a decision produces an ADR rather than a
quiet edit.
