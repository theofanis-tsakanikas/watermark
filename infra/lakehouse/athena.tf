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

resource "aws_athena_named_query" "settlement_hourly" {
  name        = "${var.project}-settlement-hourly"
  description = "Hourly settled energy per meter. Newest revision wins; summing a window and its restatement double-counts the interval."
  workgroup   = aws_athena_workgroup.main.id
  database    = aws_glue_catalog_database.gold.name
  query       = file("${path.module}/../../queries/settlement_hourly.sql")
}

resource "aws_athena_named_query" "unattributed_meters" {
  name        = "${var.project}-unattributed-meters"
  description = "Settled energy with no balancing group in force. The other half of the group query's WHERE clause, so the two reconcile without anybody having to think of the subtraction."
  workgroup   = aws_athena_workgroup.main.id
  database    = aws_glue_catalog_database.gold.name
  query       = file("${path.module}/../../queries/unattributed_meters.sql")
}
