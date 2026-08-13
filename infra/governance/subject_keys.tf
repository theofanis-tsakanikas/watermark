# One KMS key per data subject. Claim 6's mechanism, and the thing that was missing.
#
# `infra/foundation/kms.tf` creates the *root* of the hierarchy and describes it as the key
# "every subject key is derived under". The erasure state machine resolves
# `alias/${project}-subject-<subject_id>` and schedules that key for deletion. Between those two
# statements there was nothing: no per-subject key was ever created, so the first live erasure
# answered
#
#     Kms.NotFoundException: Alias alias/watermark-subject-C00007 is not found
#
# and the run refused to certify. The refusal was correct and the reason was not — it refused
# because the mechanism did not exist, not because a leg had genuinely failed, and a claim that
# only ever fails is not a claim that has been demonstrated.
#
# **Why a whole KMS key and not a data key.** Crypto-shredding needs the *key material* to
# become unavailable. A data key wrapped under the root and stored somewhere can be deleted, but
# proving that no copy of the plaintext key survives is a statement about every place it was
# ever unwrapped. `ScheduleKeyDeletion` on a customer master key is a statement AWS makes and
# can be asked to confirm, which is what a completeness proof needs.
#
# **This does not scale, and the repository says so rather than discovering it in production.**
# One key per subject at 250,000 meters is 250,000 keys: past the default account quota, and at
# roughly one dollar per key per month, an eight-figure annual bill for the encryption alone.
# See ADR-0009. The scenario's forty-one subjects are the whole of what this estate carries, and
# a production system needs the envelope design that ADR argues for.
resource "aws_kms_key" "subject" {
  for_each = toset(var.subjects)

  description = "${var.project} crypto-shredding key for data subject ${each.key} — claim 6"

  #checkov:skip=CKV_AWS_7:Rotation is the one property this key must not have, for the same reason the root key states: rotation retains the previous backing key so old ciphertext stays readable, and a shredding key that keeps a copy shreds nothing.
  enable_key_rotation = false

  # Seven days, the shortest AWS allows, and deliberately shorter than the root's thirty. An
  # erasure request has a legal clock on it (GDPR Art. 12(3): one month), and a subject key that
  # lingers for thirty days spends most of that clock in a state where the data is still
  # readable. The root is different because destroying it ends every subject at once.
  deletion_window_in_days = 7

  tags = {
    "watermark:expires-at" = var.expires_at
    "watermark:subject"    = each.key
  }
}

resource "aws_kms_alias" "subject" {
  for_each = toset(var.subjects)

  name          = "alias/${var.project}-subject-${each.key}"
  target_key_id = aws_kms_key.subject[each.key].key_id
}

# A policy on every subject key, for the reason checkov gives and one more.
#
# Without one, a KMS key falls back to "whatever IAM says", and IAM here says that several
# principals may use any key in the account. A crypto-shredding key whose destruction is the
# only thing standing between a subject and their data should not be reachable by a role that
# happens to have a broad `kms:*`.
#
# The account root keeps administrative access, because a key nobody can administer is a key
# nobody can recover from a mistake, and AWS refuses a policy that locks itself out.
data "aws_iam_policy_document" "subject_key" {
  #checkov:skip=CKV_AWS_111:A KMS key policy's Resource is always "*" and always means this key. It cannot name its own ARN, and naming any other would have no effect.
  #checkov:skip=CKV_AWS_356:As above.
  #checkov:skip=CKV_AWS_109:The root statement is what keeps the key administrable; omitting it orphans the key, and AWS refuses a policy that locks itself out. The second statement is already the narrowest useful pair of actions.
  statement {
    sid       = "AccountRootAdministers"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid    = "TheErasureOrchestrationMayShredIt"
    effect = "Allow"
    # Describe to resolve the alias, schedule-deletion to destroy it, and nothing else. This
    # role never encrypts or decrypts with a subject key: it ends it.
    actions   = ["kms:DescribeKey", "kms:ScheduleKeyDeletion"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.erasure.arn]
    }
  }
}

resource "aws_kms_key_policy" "subject" {
  for_each = toset(var.subjects)

  key_id = aws_kms_key.subject[each.key].id
  policy = data.aws_iam_policy_document.subject_key.json
}
