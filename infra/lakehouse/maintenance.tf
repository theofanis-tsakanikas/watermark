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
    # A literal, because the table is no longer a Terraform resource to reference: the job
    # itself creates it, for the reason ADR-0008 gives. This argument and the CREATE TABLE in
    # `pipelines/jobs/land_to_silver.py` are the two halves of one name, and
    # `scripts/check_lakehouse_wiring.py` is what keeps them the same name.
    "--TABLE" = "meter_interval"

    # The catalog, applied before the Spark session exists. `spark.sql.extensions` is a static
    # config and Iceberg's MERGE syntax comes from it, so a job that sets it from Python gets a
    # session that cannot parse the statement it was written for.
    "--conf" = join(" --conf ", [
      "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
      "spark.sql.catalog.glue_catalog=org.apache.iceberg.spark.SparkCatalog",
      "spark.sql.catalog.glue_catalog.warehouse=${local.warehouse}",
      "spark.sql.catalog.glue_catalog.catalog-impl=org.apache.iceberg.aws.glue.GlueCatalog",
      "spark.sql.catalog.glue_catalog.io-impl=org.apache.iceberg.aws.s3.S3FileIO",
    ])
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
      # The landing prefix, which `land_to_silver` reads and which this list did not name. The
      # job was written when its input was a table; it now reads the files the Flink sink
      # writes, and a role granted the warehouse and not the landing zone is a merge that fails
      # on `AccessDenied` at the moment it finally has something to merge. The mirror image of
      # the sink being granted a prefix it did not write to, one layer over.
      "${data.aws_s3_bucket.lakehouse.arn}/landing/*",
    ]
  }

  # IAM is only half of it. See `aws_lakeformation_permissions.maintenance_*` below.
  statement {
    # `CreateTable` is here because of ADR-0008: the job creates the Iceberg table it writes,
    # since Terraform cannot. Without it the `CREATE TABLE IF NOT EXISTS` fails with
    # `AccessDeniedException` inside a Glue cluster that is already being paid for — the same
    # shape of failure as the missing `GetSecurityConfiguration` below, which is how this list
    # got its last entry.
    #
    # It is scoped to `${var.project}_*` like everything else here: the job may create tables in
    # this estate's databases and nowhere else.
    sid    = "ReadCreateAndUpdateTheCatalog"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:CreateTable",
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

  # The job has to read the security configuration it was created with.
  #
  # This is the fifth time this repository has been caught by the same shape — a resource
  # created with a reference the role may not follow — and it is the one that stopped
  # `land_to_silver` on the first live run: *"error while getting security configuration for
  # watermark-maintenance"*. Every job above names `aws_glue_security_configuration.maintenance`,
  # Terraform applies cleanly because attaching a configuration is the *deploy* role's action,
  # and then the job role reaches its first line and cannot fetch the document that tells it how
  # to encrypt anything.
  #
  # `*` is not laziness. `glue:GetSecurityConfiguration` has no resource type in the Glue action
  # table — the configurations are account-scoped and the action rejects an ARN — so a narrower
  # grant is one that silently matches nothing. What bounds it is that reading an encryption
  # configuration discloses key ARNs, and the keys themselves are guarded by the statement above.
  statement {
    sid       = "ReadTheSecurityConfigurationItWasCreatedWith"
    effect    = "Allow"
    actions   = ["glue:GetSecurityConfiguration", "glue:GetSecurityConfigurations"]
    resources = ["*"]
  }

  statement {
    sid    = "WriteItsOwnLogs"
    effect = "Allow"
    # `AssociateKmsKey` because the security configuration encrypts Glue's logs, and Glue
    # attaches the key to the log group as the *job role* — so a role that may create the group
    # and not attach the key cannot start at all. The job never reaches its first line, and the
    # error names the log group rather than the missing action.
    actions = [
      "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
      "logs:AssociateKmsKey",
    ]
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

# **Lake Formation, which is a second and independent yes.**
#
# This account's data lake settings grant `CreateDatabaseDefaultPermissions` and
# `CreateTableDefaultPermissions` to nobody — the strict posture, and the right one, because the
# alternative is `IAMAllowedPrincipals` and a catalogue where IAM silently decides everything and
# the tag policy in `infra/governance/` is decoration.
#
# The consequence is that an IAM policy allowing `glue:CreateTable` is not enough and does not
# look insufficient. The job's first statement failed with:
#
#     Insufficient Lake Formation permission(s): Required Describe on meter_interval
#
# on a table that did not exist yet — Lake Formation answers a lookup the principal may not
# perform with "insufficient permission" rather than "not found", which is correct (the
# alternative leaks the catalogue) and reads like a bug.
#
# These grants live beside the job rather than in `infra/governance/` on purpose. Governance
# owns *who may read what kind of data* — the tag policy, the steward grants, the purpose
# limitation. This is a service role being allowed to write the table it exists to write, which
# is part of the job's definition and moves when the job moves. Splitting them the other way
# would put an operational permission in the file where a reader looks for policy.
resource "aws_lakeformation_permissions" "maintenance_database" {
  principal   = aws_iam_role.maintenance.arn
  permissions = ["CREATE_TABLE", "DESCRIBE"]

  database {
    name = aws_glue_catalog_database.silver.name
  }
}

resource "aws_lakeformation_permissions" "maintenance_tables" {
  principal = aws_iam_role.maintenance.arn

  # No `DROP`. The maintenance jobs create, read, merge and rewrite; none of them removes a
  # table, and the one operation that removes rows — the erasure path — is granted separately in
  # `infra/governance/` to the state machine that orchestrates it. A role that can drop the
  # settlement table to fix a compaction is a role that will.
  permissions = ["SELECT", "INSERT", "DELETE", "DESCRIBE", "ALTER"]

  table {
    database_name = aws_glue_catalog_database.silver.name
    wildcard      = true
  }
}
