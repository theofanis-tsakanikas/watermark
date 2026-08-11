# ADR-0007 — The framework carries records; it does not carry semantics

**Status:** accepted 2026-08-11 · **Serves:** claims 1 and 2 · **Found by:** the first live run

## Context

Two things the design assumed PyFlink could do, it cannot. Both were discovered by deploying,
neither is a schema error, and `terraform validate`, checkov and the whole offline suite were
green while both were false.

**PyFlink cannot emit a watermark.** The job was written around
`WatermarkStrategy.for_generator` — deliberately *not* the `for_bounded_out_of_orderness`
convenience constructor, because that one holds the out-of-orderness bound inside Flink where no
offline test can read it. The first live run answered:

```
AttributeError: type object 'WatermarkStrategy' has no attribute 'for_generator'
```

Flink's documentation is unambiguous: *"Currently, the Python API for Apache Flink does not
support custom watermark generation."* The choice was Flink's generator, or none.

**PyFlink could not write Iceberg.** Four classpath layers were attempted — a single `jarfile`,
the AWS bundle, a Hadoop `Configuration`, a merged uber-jar. Writing Iceberg from PyFlink needs a
catalog factory resolved in the driver, a platform that loads exactly one jar, and Hadoop classes
for a catalog that is Glue.

Read together, these are not two unrelated defects. They are the same fact twice: **the Python
surface of this framework is a transport API, and everything this project treats as semantics
lives above it.**

## Decision

**The Flink job carries records. It decides nothing.**

*No watermark strategy is attached at all.* `MeterWindowOperator` calls `observe()` from
`watermark.core.watermarks` and computes the watermark, the lag, the lateness and the closure
itself. Flink supplies transport, keying and state — not event-time semantics.

*The job does not write the lakehouse.* It writes closed windows as JSON lines to a landing
prefix using Flink's own `FileSink`, and `pipelines/jobs/land_to_silver.py` — a Glue job, where
Iceberg is native and there is no classpath to assemble — merges them into the silver table on
`(meter_id, interval_start)`, newer revision winning.

## Why this is the stronger answer, not a concession

It would be easy to write this ADR as a workaround for a framework limitation. It is the
opposite, and the distinction matters enough to state plainly.

The project's central claim is that **deterministic code owns whether a window is closed.**
Flink deciding it is no better than a PyFlink constructor deciding it — in both cases the answer
to "why did this window close?" lives somewhere no unit test can reach and no reviewer can read.
Having no strategy at all means the closure decision is in
[`src/watermark/core/watermarks.py`](../../src/watermark/core/watermarks.py), which is a pure
function over plain data, covered by `evals/watermark`, and readable in an afternoon.

**Claim 1 stays provable offline because the thing being proved never moved into the
framework.** That is precisely what `scripts/check_adapter_is_thin.py` enforces, and why it
refuses both convenience constructors by name.

The same is true of the landing zone. A `MERGE INTO` in SQL over a landing prefix is a
restatement anybody can read, re-run and diff. An Iceberg sink buried in a Flink classpath is
not, and claim 2 is a claim about bytes.

## Consequences

- **`ctx.timestamp()` is `None`.** With no strategy attached, Flink assigns no event timestamp.
  Ingestion time — which `observe()` needs, and only for stall detection — comes from
  `ctx.timer_service().current_processing_time()`. The core still reads no clock; the adapter
  reads one and passes the result in as a fact, which is the arrangement ADR-0003 already
  requires.
- **The landing hop is a real hop, with real latency**, and settlement is downstream of a Glue
  job rather than of the stream. That is acceptable because settlement's horizon is days. It
  would not be acceptable for curtailment, and curtailment does not go this way.
- **There is a window between landing and merge in which a closed window exists as JSON and not
  as a row.** Nothing may read the landing prefix as if it were the table. The silver table is
  the only source for settlement, and `pipelines/dbt/models/silver/sources.yml` names it.
- **The Glue job is now load-bearing for claims 2 and 6**, which is a dependency the original
  design did not have. It earned its own failure on the first run — a missing
  `glue:GetSecurityConfiguration` on the job role, fixed in `infra/lakehouse/maintenance.tf`.

## What this does not say

It does not say PyFlink is the wrong tool. It handles transport, keying, checkpointing and
state at a scale nothing in this repository would want to reimplement, and that is most of what
a streaming platform is.

It says that the part this project is *about* — what has been seen, and whether that is enough
to decide — was never going to survive being expressed in a framework's Python bindings, and
that finding out by deploying was worth more than the offline suite could have told us. See
`docs/DECISIONS.md` 17, which retracts the decision that said otherwise.
