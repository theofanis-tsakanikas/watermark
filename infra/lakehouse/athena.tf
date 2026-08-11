# One workgroup, with the client's own result location overridden and a bytes-scanned ceiling.
#
# `enforce_workgroup_configuration` is the setting that matters: without it a client can supply
# its own output location, and query results — which contain the rows, not a reference to them —
# land in a bucket this estate does not encrypt, does not log and does not delete.

resource "aws_athena_workgroup" "main" {
  name        = var.project
  description = "Settlement and lakehouse queries"
  state       = "ENABLED"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.athena_bytes_scanned_cutoff

    result_configuration {
      output_location = "s3://${data.aws_s3_bucket.lakehouse.id}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = data.aws_kms_key.data.arn
      }
    }

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }
  }

  # Results are the rows. Keeping them indefinitely doubles the surface an erasure request has
  # to reach for no benefit — claim 6 already has enough legs — so they expire, and the
  # lifecycle rule that expires them lives beside the bucket in `infra/foundation`.
  force_destroy = true
}

# The schema names the queries read, rendered from the databases this layer creates.
#
# `file()` used to be `file()`, and the queries wrote their schemas out by hand. Two things were
# wrong with that at once and only one of them was visible: `settlement_hourly.sql` read
# `gold.meter_interval` when `meter_interval` is created in *silver*, and every query named a
# database `gold` or `silver` when the databases are `${var.project}_gold` and
# `${var.project}_silver`. The first live run of the named query answered
# `SCHEMA_NOT_FOUND: Schema 'gold' does not exist`.
#
# `templatefile()` makes both impossible rather than fixed: the schema in the SQL is now the
# name of the database resource beside it, so renaming the project or moving a table between
# layers moves the query with it. `scripts/check_lakehouse_wiring.py` checks the *layer* each
# table is read from, which is the half templating cannot catch.
locals {
  query_schemas = {
    bronze = aws_glue_catalog_database.bronze.name
    silver = aws_glue_catalog_database.silver.name
    gold   = aws_glue_catalog_database.gold.name
  }
}

resource "aws_athena_named_query" "settlement_hourly" {
  name        = "${var.project}-settlement-hourly"
  description = "Hourly settled energy per meter. Newest revision wins; summing a window and its restatement double-counts the interval."
  workgroup   = aws_athena_workgroup.main.id
  database    = aws_glue_catalog_database.gold.name
  query       = templatefile("${path.module}/../../queries/settlement_hourly.sql", local.query_schemas)
}

resource "aws_athena_named_query" "unattributed_meters" {
  name        = "${var.project}-unattributed-meters"
  description = "Settled energy with no balancing group in force. The other half of the group query's WHERE clause, so the two reconcile without anybody having to think of the subtraction."
  workgroup   = aws_athena_workgroup.main.id
  database    = aws_glue_catalog_database.gold.name
  query       = templatefile("${path.module}/../../queries/unattributed_meters.sql", local.query_schemas)
}
