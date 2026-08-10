# The lakehouse bucket and the evidence bucket, both private, both versioned, both KMS.
#
# Versioning is not a backup here — it is what makes doctrine 4 physically true. A restatement
# writes a new Iceberg snapshot and the previous data files stay referenced by the previous
# snapshot; without versioning, an over-eager lifecycle rule can remove the object a published
# number was computed from, and the number becomes unreproducible without anything failing.

locals {
  lakehouse_bucket = "${var.project}-lakehouse-${data.aws_caller_identity.current.account_id}"
  evidence_bucket  = "${var.project}-evidence-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "lakehouse" {
  bucket = local.lakehouse_bucket

  #checkov:skip=CKV_AWS_144:A single-Region estate that is never applied and, if it were, would exist for the length of one capture. Cross-region replication guards against a Regional loss whose recovery has never been exercised, which makes it a control in name only. Versioning, which every restatement exercises, stays.
  #checkov:skip=CKV2_AWS_62:Nothing consumes object events. A notification configuration with no consumer cannot fail, and therefore proves nothing.
}

resource "aws_s3_bucket_public_access_block" "lakehouse" {
  bucket                  = aws_s3_bucket.lakehouse.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_logging" "lakehouse" {
  bucket        = aws_s3_bucket.lakehouse.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "lakehouse/"
}

resource "aws_s3_bucket_lifecycle_configuration" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }

  # Deliberately no expiration on current versions. Iceberg's own maintenance decides what is
  # unreferenced (ADR-0002 chose to own those three jobs); a lifecycle rule deleting objects
  # underneath a table format that tracks them by manifest is how a snapshot becomes a set of
  # dangling pointers, and the failure surfaces as a query returning fewer rows.
  rule {
    id     = "retire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
}

resource "aws_s3_bucket" "access_logs" {
  bucket = "${var.project}-access-logs-${data.aws_caller_identity.current.account_id}"

  #checkov:skip=CKV_AWS_18:This IS the access-log bucket. A bucket logging to itself is a loop: each log write is an event that writes another log.
  #checkov:skip=CKV_AWS_144:Access logs are not worth a second Region.
  #checkov:skip=CKV_AWS_145:SSE-S3 deliberately. S3 server access logging cannot deliver into a bucket encrypted with a KMS key it holds no grant for, and granting the logging service use of the data key widens that key to gain nothing — the logs record who touched the lakehouse, not what is in it.
  #checkov:skip=CKV2_AWS_62:A notification per log object, consumed by nothing.
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration { days = 90 }
    noncurrent_version_expiration { noncurrent_days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

# Keyed on a name this file already knows, not on an ARN the API has yet to return.
#
# `for_each = toset([aws_s3_bucket.lakehouse.arn, ...])` reads naturally and cannot plan: an
# ARN is known only after apply, and Terraform has to know every `for_each` key before it can
# draw the graph. Nothing offline catches this — `terraform validate` checks that attributes
# exist, and the first plan against a real account is where it appears. It appeared on the
# first one this project ever ran.
#
# The keys are the *logical* names; the ARN is reconstructed inside the body, where an
# unknown value is fine.
locals {
  bucket_arns = {
    lakehouse   = aws_s3_bucket.lakehouse.arn
    access_logs = aws_s3_bucket.access_logs.arn
  }
}

data "aws_iam_policy_document" "deny_plaintext" {
  for_each = toset(["lakehouse", "access_logs"])

  statement {
    sid       = "DenyUnencryptedTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [local.bucket_arns[each.key], "${local.bucket_arns[each.key]}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id
  policy = data.aws_iam_policy_document.deny_plaintext["lakehouse"].json
}

resource "aws_s3_bucket_policy" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  policy = data.aws_iam_policy_document.deny_plaintext["access_logs"].json
}
