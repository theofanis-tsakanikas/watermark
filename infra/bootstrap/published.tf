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

# The one published value that is personal data.
#
# Everything above is a name this layer chose. An address belongs to a person, and this project
# fails closed on personal data rather than reasoning about who can already read the account.
#
# Encrypted with this layer's own key rather than the AWS-managed SSM one. Same layer, same
# lifecycle, same blast radius — and the deploy role's grant to decrypt it already exists as
# `UseTheStateKey`, so the alternative was a second grant on a key nobody here controls, to
# save a euro a month on a key that is already being paid for.
resource "aws_ssm_parameter" "budget_alert_email" {
  name        = "/${var.project}/bootstrap/budget_alert_email"
  description = "Destination for the foundation layer's budget alarm."
  type        = "SecureString"
  key_id      = aws_kms_key.state.arn
  value       = var.budget_alert_email
}
