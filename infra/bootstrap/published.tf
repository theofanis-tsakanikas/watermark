# What the deploy needs to know, published where the deploy can read it.
#
# Terraform outputs live in this layer's state file, which sits on the laptop that applied it.
# CI never sees them. So without this file every value below has to be transcribed into
# repository configuration by hand — and a transcribed value looks like an independent setting.
# Rename the state bucket here and the deploy fails on a backend nobody can find, with the fix
# in a settings page rather than in a diff.
#
# Published instead, and read back at run time. What *cannot* be published is the account id:
# CI has to know which account before it can ask that account anything, and reading a parameter
# is already asking. That one stays a repository variable — one irreducible value rather than
# four transcribed ones.
#
# The path is `/<project>/bootstrap/<name>`, which is also the prefix the deploy role is granted
# and nothing wider. A grant on `/watermark/*` would let a compromised deploy read every
# parameter any later layer ever writes.

locals {
  published = {
    state_bucket      = aws_s3_bucket.state.id
    state_kms_key_arn = aws_kms_key.state.arn
    deploy_role_arn   = aws_iam_role.deploy.arn
  }
}

resource "aws_ssm_parameter" "published" {
  #checkov:skip=CKV2_AWS_34:A bucket name, a key ARN and a role ARN. None is a secret — the boundary is the OIDC trust policy, scoped to one repository and one environment, not the confidentiality of these three strings. The one value here that *is* personal data is a SecureString below.
  #checkov:skip=CKV_AWS_337:Same reason. A customer-managed key to encrypt a bucket name buys nothing and costs a euro a month.
  for_each = local.published

  name        = "/${var.project}/bootstrap/${each.key}"
  description = "Published by infra/bootstrap so the deploy resolves it instead of transcribing it."
  type        = "String"
  value       = each.value
}

# `budget_alert_email` is deliberately *not* published.
#
# It was, briefly, as a SecureString — because the budget lived in `foundation` and every deploy
# needed it. The budget now lives in this layer, so the address is consumed by the same apply
# that takes it in and never leaves this state file. An address belongs to a person rather than
# to the account, and the safest place for personal data is the one where it does not have to
# travel at all.
#
# It stays out of `terraform.tfvars` for the same reason. Pass it on the command line.
