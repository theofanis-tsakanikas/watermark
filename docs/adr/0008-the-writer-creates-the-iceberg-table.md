# ADR-0008 — The writer creates the Iceberg table

**Status:** accepted · **Date:** 2026-08-12 · **Supersedes part of** [ADR-0002](0002-iceberg-on-s3-over-s3-tables.md)

## Context

`infra/lakehouse/glue.tf` declared every lakehouse table as an `aws_glue_catalog_table` carrying
`table_type = "ICEBERG"` in its parameters, alongside a full column list, a location under the
warehouse prefix and the shared property block. It validated, it scanned clean, it applied
without error, and the tables appeared in the Glue catalogue with exactly the schema the
repository intended.

None of them was an Iceberg table.

An Iceberg table **is** its metadata: a JSON file in the warehouse that names the current
snapshot, the schema, the partition spec and the manifest list. The Glue catalogue entry is a
pointer to that file, held in a `metadata_location` parameter. Terraform can write the pointer's
neighbours and it cannot write the file, because writing it means computing a snapshot — which
is what an engine does. So what `aws_glue_catalog_table` produced was an entry with the shape of
an Iceberg table and no metadata location, and Athena refuses it by name:

```
GENERIC_USER_ERROR: Detected Iceberg type table without metadata location. Please make sure an
Iceberg-enabled compute engine such as Athena or EMR Spark is used to create the table, or the
table is created by using the Iceberg open source AWS library iceberg-aws. Setting table_type
parameter in Glue metastore to create an Iceberg table is not supported.
```

Verified against a deployed estate on 2026-08-12. The `warehouse/` prefix of a fully applied,
fully green estate was **empty**: no metadata, no manifests, no data. Every query against these
tables had failed for as long as the tables had existed, and nothing had ever run one.

This is not a provider gap to work around. It is AWS documenting that the operation does not
exist.

## Decision

**The engine that writes a table creates it.** Terraform owns everything it can actually own —
the databases, the warehouse location, the bucket, the KMS key, the job definitions, the IAM —
and stops declaring the tables an Iceberg engine writes.

- `silver.meter_interval` is created by `pipelines/jobs/land_to_silver.py`, with
  `CREATE TABLE IF NOT EXISTS ... USING iceberg`, immediately before the `MERGE INTO` that
  writes it.
- `gold.settlement_hour` and the models beside it are created by dbt, which already sets
  `+table_type: iceberg` in `dbt_project.yml` and has always built its own tables.
- The catalogue entries that remain in `glue.tf` describe tables a CDC pipeline lands. They
  carry the same flaw and it is stated in the file rather than fixed: nothing in this repository
  writes them, so nothing has created their metadata either.

Three consequences follow, and each is a change somewhere else:

**The schema moves to the writer.** `land_to_silver.py` now carries the column list. That is a
loss — the schema was more readable in HCL beside the property block — and it is the price of
the schema being in the one place that can enforce it. A `CREATE TABLE` that disagrees with the
`MERGE` below it fails on the same run rather than on a query somebody makes a week later.

**A data quality ruleset cannot be applied at the same time as the job it checks.**
`aws_glue_data_quality_ruleset` attaches to a table, and after this decision the table does not
exist when the lakehouse layer applies. The rulesets moved to `infra/governance/`, which applies
last, and `deploy.yml` runs `land-to-silver` once with nothing landed between the two layers for
the sole purpose of bringing the table into existence. The merge job returns early and cleanly
when the landing prefix is empty, which it already did.

**`gold.settlement_hour` lost its ruleset entirely**, and that is recorded as a gap rather than
a decision: dbt cannot build the table until silver holds rows, so there is no point in a deploy
at which it exists. It returns when the gold layer is built inside the capture.

## Alternatives rejected

**Have the job drop the Terraform stub and re-create it properly.** It would have worked once
and then fought Terraform for ever: the next `apply` sees a table whose parameters have gained a
`metadata_location`, calls it drift, and removes the pointer — destroying the table by
correcting it. Suppressing that with `ignore_changes` means a resource Terraform manages and
does not manage, which is worse than one it does not manage.

**Run the DDL from `deploy.yml` as an Athena statement.** Honest and simple, and it puts the
schema in a third place — neither the writer nor Terraform — so a column added to the merge
would still be added twice. The current arrangement has the schema exactly once.

**Give up Iceberg for Hive tables, which Terraform can genuinely declare.** This would trade the
whole of claim 6 for a tidier `glue.tf`. Row-level deletes against a data subject are why
ADR-0002 chose Iceberg, and a Hive-format table is a prefix that erasure can only reach by
rewriting it.

## What this costs

`CLAUDE.md` says *"IaC only. Every cloud resource in Terraform. No console deployments, ever."*
That rule stands, and this is not an exception to it: nothing here is created by hand or from a
console, and the DDL runs inside the same gated workflow as everything else. What changes is
*which* code declares a table — Terraform for the resources AWS lets Terraform create, and the
writer for the ones it does not.

The rule that would have caught this earlier is not a new rule. It is
`docs/AWS-CONSTRAINTS.md`'s: a service fact that makes a design impossible rather than merely
different has to be verified before the design leans on it. The constraint document covers
Feature Store's identifiers, Managed Flink's packaging, Kinesis's shard model and Iceberg
maintenance. It did not cover how an Iceberg table comes into existence, because that looked
like the part nobody could get wrong.
