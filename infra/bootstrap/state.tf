# The remote state backend: one bucket, one KMS key, and S3's own locking.
#
# There is no DynamoDB lock table. Terraform's S3 backend has supported locking through a lock
# file in the state bucket since 1.10 (`use_lockfile = true`), which removes a table, its
# capacity mode, its own encryption decision and one more thing to destroy — while putting the
# lock in the same place, under the same key, as the thing it protects. `required_version` in
# versions.tf is what stops an older Terraform silently applying with no lock at all.

data "aws_caller_identity" "current" {}

locals {
  # Account-suffixed because S3 bucket names are globally unique, and a name that collides
  # with a stranger's bucket fails an apply in a way that reads like a permissions problem.
  state_bucket = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}"
  logs_bucket  = "${var.project}-tfstate-logs-${data.aws_caller_identity.current.account_id}"
}

resource "aws_kms_key" "state" {
  description             = "Encrypts Terraform state for ${var.project}"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

# An explicit key policy, in its own resource so that it can name the deploy role without the
# key and the role depending on each other.
#
# A key with no policy gets a default that grants the account root full access, which is not
# wrong but is also not written down anywhere a reviewer reads. The root statement below is
# not optional in the explicit version: omit it and the key becomes unmanageable by anyone,
# including the person who has to rotate or delete it.
data "aws_iam_policy_document" "state_key" {
  # These three read an IAM *identity* policy. This is a KMS *key* policy, where `Resource: "*"`
  # is the only valid form and means "the key this policy is attached to" — a key policy cannot
  # name its own ARN, and naming any other ARN would have no effect. Rewriting it to satisfy
  # the check is not possible; suppressing it without saying so is how a scanner stops meaning
  # anything. The scope is constrained where it can be: by the principals.
  #checkov:skip=CKV_AWS_111:A KMS key policy's Resource is always "*" and always means this key.
  #checkov:skip=CKV_AWS_356:As above — "*" here is the key itself, not every key.
  #checkov:skip=CKV_AWS_109:The root statement is what keeps the key administrable; omitting it orphans the key.

  statement {
    sid       = "AccountRootAdministersTheKey"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid    = "DeployRoleUsesTheKeyForState"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.deploy.arn]
    }
  }
}

resource "aws_kms_key_policy" "state" {
  key_id = aws_kms_key.state.id
  policy = data.aws_iam_policy_document.state_key.json
}

resource "aws_kms_alias" "state" {
  name          = "alias/${var.project}-tfstate"
  target_key_id = aws_kms_key.state.key_id
}

# ── Access logs ──────────────────────────────────────────────────────────────
# A separate bucket, because a bucket cannot usefully log to itself: each write of a log
# object is an event that produces another log object.

resource "aws_s3_bucket" "logs" {
  bucket = local.logs_bucket

  #checkov:skip=CKV_AWS_18:This IS the access-log bucket. A bucket logging to itself is a loop: each log write is an event that writes another log.
  #checkov:skip=CKV_AWS_144:Cross-region replication of access logs buys recovery from a Regional loss of the record of who touched state. The state itself is versioned and every layer is reproducible from this repository; the logs are not worth a second Region.
  #checkov:skip=CKV_AWS_145:SSE-S3 deliberately. S3 server access logging cannot deliver into a bucket encrypted with a KMS key it holds no grant for, and granting the logging service use of the state key widens that key to gain nothing — the logs record who touched state, not what it contains.
  #checkov:skip=CKV2_AWS_62:Event notifications on an access-log bucket would be a notification per log object. Nothing consumes them and the volume is the point of the bucket.
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3, not the customer-managed key. S3 server access logging cannot deliver to a
      # bucket encrypted with a KMS key it has no grant for, and granting the logging service
      # use of the state key widens that key's blast radius to gain nothing: the logs say who
      # touched state, not what the state contains.
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expire-access-logs"
    status = "Enabled"
    filter {}

    expiration {
      days = var.state_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ── State ────────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  # State is the one thing here that is genuinely hard to reconstruct: it is the record of
  # which real resources exist. Losing it does not lose the configuration, it loses the
  # mapping, and an apply against a lost mapping creates a second estate beside the first.
  lifecycle {
    prevent_destroy = true
  }

  #checkov:skip=CKV_AWS_144:A single-Region estate that exists for the length of one capture. Cross-region replication would guard against losing the mapping to a Regional failure, at the cost of a second Region's storage and a replication role — and the recovery it enables has never been exercised, which makes it a control in name. Versioning, which has been exercised, stays.
  #checkov:skip=CKV2_AWS_62:Nothing consumes state-object events. A notification configuration with no consumer is a control that cannot fail and therefore proves nothing.
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_logging" "state" {
  bucket        = aws_s3_bucket.state.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "tfstate/"
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "retire-superseded-state"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.state_retention_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# TLS is not optional, and the default bucket policy does not say so.
data "aws_iam_policy_document" "state" {
  statement {
    sid     = "DenyUnencryptedTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
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

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state.json
}
