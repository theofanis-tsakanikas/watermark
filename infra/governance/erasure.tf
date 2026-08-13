# The erasure orchestration. Claim 6, and the honesty that claim rests on.
#
# The state machine has one property that matters more than any of its steps: **it refuses to
# emit a certificate unless every leg confirms.** A partial erasure reported as complete is
# worse than no erasure at all, because the subject is told they are gone and the residual is
# now invisible.
#
# Five legs, and they are not equal.
#
#   1. **Crypto-shred.** Destroy the subject's key. Everything encrypted under it — the
#      lakehouse rows, the offline store records — becomes unreadable at once. Fast, and
#      verifiable by attempting to read.
#   2. **Physical deletion.** Row-level deletes for anything the key hierarchy does not cover,
#      then compaction, then orphan-file removal. This is why ADR-0002 kept the maintenance
#      jobs: the certificate has to state which run removed which files, and a managed service's
#      internal schedule cannot be waited on.
#   3. **Online store.** `DeleteRecord`, which is why `is_deleted` is a reserved feature name.
#   4. **Training sets.** The snapshots a model was trained from, re-derived without the
#      subject.
#   5. **Model artefacts — the leg that cannot be completed.** A model trained before the
#      request keeps the subject's contribution in its weights. No key protects that and
#      destroying one does not remove it. This leg is satisfied by quarantining the affected
#      model and retraining from the shredded corpus, and the **residual window is printed on
#      the certificate**. Machine unlearning is not claimed and is not attempted.
#
# Leg 5 is the reason the certificate says "erased to a declared boundary" rather than
# "erased". Saying the second would be the exact overclaim this repository exists to argue
# against.

resource "aws_sfn_state_machine" "erasure" {
  #checkov:skip=CKV_AWS_285:Execution history logging IS enabled at level ALL. What is off is `include_execution_data`, deliberately: the execution input is the subject identifier of somebody exercising Art. 17, and writing it into a log group is copying the data being erased into a store the erasure does not reach. The state transitions are logged; the subject is not.
  name     = "${var.project}-erasure"
  role_arn = aws_iam_role.erasure.arn
  type     = "STANDARD"

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.erasure.arn}:*"
    include_execution_data = false # the execution data is the subject
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  definition = templatefile("${path.module}/erasure.asl.json.tftpl", {
    project              = var.project
    region               = var.aws_region
    account              = data.aws_caller_identity.current.account_id
    residual_days        = var.erasure_residual_days
    compaction_job       = "${var.project}-compaction"
    expire_snapshots_job = "${var.project}-expire-snapshots"
    orphan_files_job     = "${var.project}-delete-orphan-files"
    certificate_bucket   = data.aws_s3_bucket.lakehouse.id
  })
}

resource "aws_cloudwatch_log_group" "erasure" {
  name              = "/aws/vendedlogs/states/${var.project}-erasure"
  retention_in_days = 400
  kms_key_id        = data.aws_kms_key.logs.arn
}

resource "aws_iam_role" "erasure" {
  name = "${var.project}-erasure"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "erasure" {
  #checkov:skip=CKV_AWS_111:`glue:UpdateTable` and the Glue `Get*` calls sit on `*`. An Iceberg DELETE rewrites the table's metadata pointer, and a query planner resolves databases it was not told about in advance — Glue offers no resource form narrower than the catalogue for either. The identity is assumable only by Step Functions and runs one state machine.
  #checkov:skip=CKV_AWS_356:As above.
  #checkov:skip=CKV_AWS_290:As above.
  statement {
    sid    = "DestroyASubjectKey"
    effect = "Allow"
    # ScheduleKeyDeletion, not DisableKey. A disabled key is a key somebody re-enables, and an
    # erasure that can be undone by a console click is not an erasure. The seven-day minimum
    # window is the only pause, and it is stated on the certificate.
    actions   = ["kms:ScheduleKeyDeletion", "kms:DescribeKey", "kms:ListAliases"]
    resources = ["arn:aws:kms:${var.aws_region}:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringLike"
      variable = "kms:RequestAlias"
      values   = ["alias/${var.project}-subject-*"]
    }
  }

  statement {
    sid       = "RunTheMaintenanceItMustWaitOn"
    effect    = "Allow"
    actions   = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns"]
    resources = ["arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/${var.project}-*"]
  }

  statement {
    sid       = "DeleteFromTheOnlineStore"
    effect    = "Allow"
    actions   = ["sagemaker:DeleteRecord", "sagemaker:GetRecord", "sagemaker:DescribeFeatureGroup"]
    resources = ["arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:feature-group/${var.project}-*"]
  }

  statement {
    sid       = "RewriteTheLakehouse"
    effect    = "Allow"
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults"]
    resources = ["arn:aws:athena:${var.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.project}"]
  }

  statement {
    sid     = "WriteTheCertificate"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${data.aws_s3_bucket.lakehouse.arn}/erasure-certificates/*",
      # The shred marker, written the moment the key is scheduled and before any other leg has
      # reported. A certificate says the whole erasure completed; this says the irreversible
      # part did, which is the fact the next `terraform apply` has to know about whether or not
      # the rest of the run succeeded.
      "${data.aws_s3_bucket.lakehouse.arn}/erasure-shredded/*",
    ]
  }

  # Athena writes its results before it will start a query, and refuses with "Unable to
  # verify/create output bucket" when it cannot — a message about a bucket, caused by a missing
  # grant. The first live erasure failed both DELETE legs on it.
  #
  # `ListBucket` on the bucket itself is what the *verify* half needs; `athena-results/*` is
  # where the workgroup puts them.
  statement {
    sid    = "LetAthenaWriteItsResults"
    effect = "Allow"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload",
      "s3:ListBucketMultipartUploads", "s3:ListMultipartUploadParts",
    ]
    resources = ["${data.aws_s3_bucket.lakehouse.arn}/athena-results/*"]
  }

  statement {
    sid       = "AndFindTheBucketFirst"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [data.aws_s3_bucket.lakehouse.arn]
  }

  # **The warehouse itself, which the DELETE legs need and did not have.**
  #
  # The grants above cover Athena's own scratch space, and an erasure that only reads its
  # results is an erasure that never touched the data. The second live run failed on:
  #
  #     not authorized to perform: s3:GetObject on resource:
  #     ".../warehouse/silver/meter_interval/metadata/00004-....metadata.json"
  #
  # An Iceberg `DELETE` is not a deletion in place. It reads the current metadata to find the
  # manifests, reads the manifests to find the data files, writes new data and delete files, and
  # commits a new metadata pointer. So the role needs to read and write the table's own prefix,
  # and `DeleteObject` besides — the compaction and orphan-file jobs that follow are what
  # actually remove the superseded files, and a subject whose rows survive in an orphaned
  # Parquet file has not been erased.
  #
  # Scoped to `warehouse/*`, not the bucket: this role exists to remove one person's rows, and a
  # grant wide enough to reach the certificates it writes about itself is a grant wide enough to
  # rewrite the evidence.
  statement {
    sid       = "RewriteTheTablesItDeletesFrom"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${data.aws_s3_bucket.lakehouse.arn}/warehouse/*"]
  }

  # The results are SSE-KMS with the data key, so writing them needs the key as well as the
  # prefix. A grant on the bucket and not the key is a query that fails on encryption.
  statement {
    sid       = "AndEncryptThem"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [data.aws_kms_key.data.arn]
  }

  # Athena reads the tables through Glue, and a DELETE has to resolve them first.
  statement {
    sid    = "ResolveTheTablesItDeletesFrom"
    effect = "Allow"
    actions = [
      "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables",
      "glue:GetPartition", "glue:GetPartitions", "glue:UpdateTable",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "LogItself"
    effect    = "Allow"
    actions   = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
    resources = ["*"]
  }

  statement {
    sid       = "Trace"
    effect    = "Allow"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }
}

#checkov:skip=CKV_AWS_111:The CloudWatch Logs delivery actions and the X-Ray actions have no resource form — AWS documents both as requiring "*". Every other statement in this policy names its resources, including the KMS statement, which is additionally constrained by a RequestAlias condition to the subject-key namespace.
#checkov:skip=CKV_AWS_356:As above.
#checkov:skip=CKV_AWS_109:As above.
resource "aws_iam_role_policy" "erasure" {
  name   = "erase-a-subject"
  role   = aws_iam_role.erasure.id
  policy = data.aws_iam_policy_document.erasure.json
}
