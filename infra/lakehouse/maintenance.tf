# The three Iceberg maintenance routines, run by us.
#
# ADR-0002 chose Iceberg on S3 over S3 Tables knowing this was the bill: `rewriteDataFiles`,
# `expireSnapshots` and `deleteOrphanFiles` become jobs this repository schedules, observes and
# can invoke on demand. Two of those verbs are why the decision went this way.
#
# **Observes.** Claim 6's completeness proof has to confirm that the files holding a subject
# were rewritten, not predict that a managed service will get to it.
#
# **On demand.** The erasure Step Function calls compaction and waits. A schedule cannot be
# waited on.
#
# And the expiry job refuses to remove a *tagged* snapshot. A published settlement total is
# bound to the snapshot it was computed from; without that refusal, claim 2 has a shelf life.

# The job that makes the lakehouse a lakehouse.
#
# The Flink job writes closed windows to a landing prefix as JSON lines; this merges them into
# the silver table. Without it the platform decides correctly and stores nothing, which is what
# the first live run actually did — settlement had nothing to total, the erasure legs had
# nothing to delete, and a window that is not stored cannot be restated.
#
# It is here rather than inside the streaming job because writing Iceberg from PyFlink means a
# catalog factory resolved in the driver, a platform that loads one jar, and Hadoop classes for
# a catalog that is Glue. Glue has native Iceberg and no classpath to assemble.
resource "aws_glue_job" "land_to_silver" {
  name              = "${var.project}-land-to-silver"
  role_arn          = aws_iam_role.maintenance.arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2

  command {
    script_location = "s3://${data.aws_s3_bucket.lakehouse.id}/jobs/land_to_silver.py"
    python_version  = "3"
  }
  security_configuration = aws_glue_security_configuration.maintenance.name


  default_arguments = {
    "--enable-metrics"                   = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--datalake-formats"                 = "iceberg"
    "--WAREHOUSE"                        = local.warehouse
    "--LANDING"                          = "s3://${data.aws_s3_bucket.lakehouse.id}/landing/meter_interval/"
    "--DATABASE"                         = aws_glue_catalog_database.silver.name
    "--TABLE"                            = aws_glue_catalog_table.meter_interval.name
  }

  execution_property {
    # One at a time, like the compaction below and for the same reason: two merges over one
    # table conflict under Iceberg's optimistic writes and both eventually fail, noisily and
    # for a reason that reads like a data problem.
    max_concurrent_runs = 1
  }
}

resource "aws_glue_job" "compaction" {
  name         = "${var.project}-compaction"
  role_arn     = aws_iam_role.maintenance.arn
  glue_version = "4.0"
  worker_type  = "G.1X"
  # Two workers. 250,000 meters uploading in a burst is a small-file generator and this is the
  # operational work ADR-0002 accepted; two is enough for the estate's volume and small enough
  # that a job left running by mistake is not the thing that breaks the budget.
  number_of_workers = 2

  command {
    script_location = "s3://${data.aws_s3_bucket.lakehouse.id}/jobs/compaction.py"
    python_version  = "3"
  }

  default_arguments = {
    "--enable-metrics"                   = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--datalake-formats"                 = "iceberg"
    "--WAREHOUSE"                        = local.warehouse
    "--TARGET_FILE_SIZE_MB"              = "512"
  }

  execution_property {
    # One at a time. Iceberg arbitrates writes optimistically, so two compactions over one table
    # conflict, retry and eventually both fail — noisily, and for a reason that reads like a
    # data problem.
    max_concurrent_runs = 1
  }

  security_configuration = aws_glue_security_configuration.maintenance.name
}

resource "aws_glue_job" "expire_snapshots" {
  name              = "${var.project}-expire-snapshots"
  role_arn          = aws_iam_role.maintenance.arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2

  command {
    script_location = "s3://${data.aws_s3_bucket.lakehouse.id}/jobs/expire_snapshots.py"
    python_version  = "3"
  }

  default_arguments = {
    "--datalake-formats" = "iceberg"
    "--WAREHOUSE"        = local.warehouse
    "--MIN_SNAPSHOTS"    = "10"
    "--MAX_AGE_DAYS"     = "30"
    # The refusal. A snapshot carrying a tag was the state a published number came from, and
    # removing it makes that number unreproducible — which is claim 2 quietly expiring.
    "--REFUSE_TAGGED" = "true"
  }

  execution_property {
    max_concurrent_runs = 1
  }

  security_configuration = aws_glue_security_configuration.maintenance.name
}

resource "aws_glue_job" "delete_orphan_files" {
  name              = "${var.project}-delete-orphan-files"
  role_arn          = aws_iam_role.maintenance.arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2

  command {
    script_location = "s3://${data.aws_s3_bucket.lakehouse.id}/jobs/delete_orphan_files.py"
    python_version  = "3"
  }

  default_arguments = {
    "--datalake-formats" = "iceberg"
    "--WAREHOUSE"        = local.warehouse
    # Three days. Shorter risks deleting a file a long-running write has produced and not yet
    # committed, which turns a slow job into a corrupt table.
    "--OLDER_THAN_DAYS" = "3"
  }

  execution_property {
    max_concurrent_runs = 1
  }

  security_configuration = aws_glue_security_configuration.maintenance.name
}

resource "aws_glue_security_configuration" "maintenance" {
  name = "${var.project}-maintenance"

  encryption_configuration {
    cloudwatch_encryption {
      cloudwatch_encryption_mode = "SSE-KMS"
      kms_key_arn                = data.aws_kms_key.logs.arn
    }
    job_bookmarks_encryption {
      job_bookmarks_encryption_mode = "CSE-KMS"
      kms_key_arn                   = data.aws_kms_key.data.arn
    }
    s3_encryption {
      s3_encryption_mode = "SSE-KMS"
      kms_key_arn        = data.aws_kms_key.data.arn
    }
  }
}

resource "aws_iam_role" "maintenance" {
  name = "${var.project}-lakehouse-maintenance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "maintenance" {
  statement {
    sid    = "RewriteTheWarehouse"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      data.aws_s3_bucket.lakehouse.arn,
      "${data.aws_s3_bucket.lakehouse.arn}/warehouse/*",
      "${data.aws_s3_bucket.lakehouse.arn}/jobs/*",
    ]
  }

  statement {
    sid    = "ReadAndUpdateTheCatalog"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:UpdateTable",
      "glue:GetPartitions",
      "glue:BatchCreatePartition",
      "glue:BatchDeletePartition",
    ]
    resources = [
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${var.project}_*",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.project}_*/*",
    ]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [data.aws_kms_key.data.arn, data.aws_kms_key.logs.arn]
  }

  statement {
    sid       = "WriteItsOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/*"]
  }
}

resource "aws_iam_role_policy" "maintenance" {
  name   = "maintain-the-lakehouse"
  role   = aws_iam_role.maintenance.id
  policy = data.aws_iam_policy_document.maintenance.json
}

# Compaction on a schedule; the other two are invoked by the erasure orchestration and by the
# nightly window in `infra/governance`. Compaction is the only one that has to keep up with
# ingestion rather than with a request.
resource "aws_glue_trigger" "compaction" {
  name     = "${var.project}-compaction"
  type     = "SCHEDULED"
  schedule = "cron(30 * * * ? *)"
  enabled  = true

  actions {
    job_name = aws_glue_job.compaction.name
  }
}
