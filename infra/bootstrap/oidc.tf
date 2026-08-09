# The identity CI assumes. No long-lived access keys, ever.
#
# GitHub Actions presents a short-lived OIDC token; STS exchanges it for credentials that
# expire with the job. There is no secret in the repository, nothing to rotate, and nothing to
# leak — and the whole of the security of it is the `sub` condition below.

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = var.github_oidc_thumbprints
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn

  # Every trusted subject names this repository and exactly one environment. Spelled out as a
  # list rather than reached with a wildcard: `repo:owner/repo:*` trusts every branch and
  # every pull request, including one a stranger opens, and it reads as a small convenience
  # right up until it is the whole of the breach.
  trusted_subjects = [
    for environment in var.deploy_environments :
    "repo:${var.github_owner}/${var.github_repo}:environment:${environment}"
  ]
}

data "aws_iam_policy_document" "assume_from_github" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # StringEquals, not StringLike. There is no wildcard in any of these values, so the
    # weaker operator would buy nothing and would let one creep in unnoticed later.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.trusted_subjects
    }
  }
}

#checkov:skip=CKV_AWS_274:No AdministratorAccess is attached. The role holds state-backend permissions only; see the comment below.
resource "aws_iam_role" "deploy" {
  name                 = "${var.project}-deploy"
  description          = "Assumed by GitHub Actions to apply and destroy the ${var.project} estate"
  assume_role_policy   = data.aws_iam_policy_document.assume_from_github.json
  max_session_duration = 3600
}

# The role can reach the state backend and nothing else — today.
#
# The alternative, and the usual choice, is to attach AdministratorAccess now and narrow it
# "later". Later does not arrive: by the time the estate exists nobody can say which of the
# permissions are load-bearing, so the policy stays as it is and the deploy role becomes the
# most powerful identity in the account, held by a workflow anybody can trigger a run of.
#
# So each phase adds the permissions its own layer needs, in the commit that adds the layer,
# where a reviewer can see the resource and the grant side by side. A `terraform apply` that
# fails on an access denial is a cheap and legible failure; it names the action, and the fix
# is one statement with a reason.
data "aws_iam_policy_document" "state_access" {
  statement {
    sid    = "ReadWriteTerraformState"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
  }

  statement {
    sid    = "UseTheStateKey"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    resources = [aws_kms_key.state.arn]
  }
}

resource "aws_iam_role_policy" "state_access" {
  name   = "state-backend"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.state_access.json
}
