# Three keys, and the third is the one claim 6 rests on.
#
# **Logs** and **data** are ordinary: one key each, rotating, with a policy that names who may
# use them. Splitting them means a grant to the logging service does not widen the key that
# protects consumption data.
#
# **The subject key hierarchy** is different. `docs/DECISIONS.md` 11 chose crypto-shredding per
# data subject: a key per customer, wrapping the data keys that encrypt that customer's personal
# fields, so an erasure destroys one key rather than rewriting a lakehouse. What it buys is that
# erasure is *fast* and *provable* for everything the key covers.
#
# What it does not reach is stated on the same line, because this is where the temptation to
# overclaim lives: a model trained before the request keeps the subject's contribution in its
# weights, no key protects that, and destroying one does not remove it. That leg is quarantine
# plus retraining with a declared residual window. Machine unlearning is not claimed.
#
# The per-subject keys themselves are created at runtime by the erasure orchestration, not here:
# there is one per customer and Terraform is not a customer database. What this layer creates is
# the *root* they are derived under and the alias namespace the orchestration writes into.

resource "aws_kms_key" "logs" {
  description             = "${var.project} log encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${var.project}-logs"
  target_key_id = aws_kms_key.logs.key_id
}

resource "aws_kms_key" "data" {
  description             = "${var.project} lakehouse and stream encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.project}-data"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_kms_key" "subject_root" {
  description = "${var.project} root of the per-subject key hierarchy — see claim 6"

  #checkov:skip=CKV_AWS_7:Rotation is disabled deliberately and it is the whole mechanism. Rotation creates a new backing key and retains the old one so previously encrypted data stays readable — which is exactly the property crypto-shredding must not have. A rotating shredding key is a shredder that keeps a copy. See docs/DECISIONS.md 11 and the comment below.

  # Not rotated. Rotation creates a new backing key and keeps the old one so previously
  # encrypted data stays readable, which is precisely the property crypto-shredding must not
  # have: the whole mechanism is that destroying the key makes the ciphertext unreadable
  # forever. A rotating shredding key is a shredder that keeps a copy.
  enable_key_rotation = false

  # The longest window AWS allows. Deleting this key makes every subject key derived under it
  # unusable at once, which is either the largest erasure in the system's history or the worst
  # accident available in it, and thirty days is the only thing standing between the two.
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "subject_root" {
  name          = "alias/${var.project}-subject-root"
  target_key_id = aws_kms_key.subject_root.key_id
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "logs_key" {
  #checkov:skip=CKV_AWS_111:A KMS key policy's Resource is always "*" and always means this key.
  #checkov:skip=CKV_AWS_356:As above.
  #checkov:skip=CKV_AWS_109:The root statement is what keeps the key administrable; omitting it orphans it. The logs service statement below is constrained by an encryption-context condition on the log-group ARN, which is the constraint available to a key policy.
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
    sid       = "CloudWatchLogsUsesTheKey"
    effect    = "Allow"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type = "Service"
      identifiers = [
        "logs.${var.aws_region}.amazonaws.com",
        # Budgets encrypts the notification it publishes to the reaper's topic with this key.
        # Omitting it is invisible until the threshold is crossed, at which point the delivery
        # fails and the one message the control exists to send is the one nobody receives.
        "budgets.amazonaws.com",
      ]
    }
    # Without this the key is usable by the logs service for *any* log group in the account.
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:*"]
    }
  }
}

resource "aws_kms_key_policy" "logs" {
  key_id = aws_kms_key.logs.id
  policy = data.aws_iam_policy_document.logs_key.json
}


# The remaining two key policies. Each names the account root — an explicit policy that omits it
# orphans the key, including from whoever has to rotate or delete it — and nothing else yet.
# Service principals are granted use by the layer that creates the service, in the commit that
# creates it, where a reviewer can see the resource and the grant side by side.
data "aws_iam_policy_document" "account_root_only" {
  #checkov:skip=CKV_AWS_111:A KMS key policy's Resource is always "*" and always means this key. It cannot name its own ARN, and naming any other would have no effect.
  #checkov:skip=CKV_AWS_356:As above.
  #checkov:skip=CKV_AWS_109:The root statement is what keeps the key administrable; omitting it orphans it.
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
}

resource "aws_kms_key_policy" "data" {
  key_id = aws_kms_key.data.id
  policy = data.aws_iam_policy_document.account_root_only.json
}

resource "aws_kms_key_policy" "subject_root" {
  key_id = aws_kms_key.subject_root.id
  policy = data.aws_iam_policy_document.account_root_only.json
}
