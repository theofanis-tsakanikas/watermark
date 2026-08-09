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

  # Read back what `published.tf` wrote, and nothing else.
  #
  # Scoped to this layer's prefix rather than `/${var.project}/*`: later layers will publish
  # parameters of their own, and a deploy that can read all of them is a deploy that can read
  # whatever the next person puts there without anybody revisiting this grant.
  #
  # Easy to forget, and it fails in the worst place — *after* `configure-aws-credentials`
  # succeeds, when the chain looks like it worked.
  statement {
    sid       = "ReadWhatBootstrapPublished"
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/${var.project}/bootstrap/*"]
  }

  # The budget address is a SecureString encrypted with this layer's own key, so decrypting it
  # needs no grant beyond `UseTheStateKey` above. That is the reason for using that key rather
  # than the AWS-managed SSM one: one key, one grant, one thing to reason about.
}

resource "aws_iam_role_policy" "state_access" {
  name   = "state-backend"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.state_access.json
}

# ── The permissions each layer needs, added by the layer that needs them ─────
#
# The comment on `state_access` above explains why AdministratorAccess is not here. This is the
# other half of that promise: each layer's grant lives in its own statement, named after the
# layer, so a reviewer can read what a deploy is allowed to create and a `terraform apply` that
# fails on an access denial names the action it wanted.
#
# It is longer than `AdministratorAccess`. That is the trade.

data "aws_iam_policy_document" "deploy_layers" {
  # A deploy role creates resources that do not exist yet, so its statements cannot name their
  # ARNs — there is no ARN to name until the apply runs. It is constrained the two ways that are
  # available: by service, per layer, so a reviewer can read what a deploy may create; and by
  # the explicit deny at the bottom, which stops it widening its own trust or minting a
  # long-lived credential. That deny is the constraint that actually matters.
  #checkov:skip=CKV_AWS_111:Resources do not exist until the apply creates them.
  #checkov:skip=CKV_AWS_356:As above.
  #checkov:skip=CKV_AWS_109:As above — see the NeverWidenItsOwnTrust statement.
  #checkov:skip=CKV_AWS_107:The deploy role does not read credentials; the statements are creation actions.
  #checkov:skip=CKV_AWS_110:Privilege escalation through iam:* is denied explicitly below.
  #checkov:skip=CKV_AWS_108:`s3:Get*` is here so the deploy can read back what it created — an object version, a bucket policy. The data this estate holds is encrypted with a customer-managed key the deploy role has no grant on, so reading a lakehouse object returns ciphertext.
  statement {
    sid    = "Foundation"
    effect = "Allow"
    actions = [
      "ec2:*Vpc*", "ec2:*Subnet*", "ec2:*RouteTable*", "ec2:*SecurityGroup*",
      "ec2:*VpcEndpoint*", "ec2:*FlowLogs*", "ec2:Describe*", "ec2:*Tags*",
      "kms:CreateKey", "kms:CreateAlias", "kms:DeleteAlias", "kms:TagResource",
      "kms:PutKeyPolicy", "kms:ScheduleKeyDeletion", "kms:EnableKeyRotation",
      "kms:DisableKeyRotation", "kms:Describe*", "kms:List*", "kms:Get*",
      "s3:CreateBucket", "s3:DeleteBucket", "s3:Put*", "s3:Get*", "s3:List*",
      "budgets:*", "sns:*", "logs:*", "lambda:*", "events:*",
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:PassRole",
      "iam:*RolePolicy", "iam:ListRolePolicies", "iam:TagRole",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "Streaming"
    effect = "Allow"
    actions = [
      "kinesis:*", "iot:*", "glue:*Registry*", "glue:*Schema*",
      "kinesisanalytics:*", "cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms",
      "cloudwatch:DescribeAlarms",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "Lakehouse"
    effect    = "Allow"
    actions   = ["glue:*", "athena:*", "lakeformation:*"]
    resources = ["*"]
  }

  statement {
    sid       = "MachineLearning"
    effect    = "Allow"
    actions   = ["sagemaker:*"]
    resources = ["*"]
  }

  statement {
    sid       = "Governance"
    effect    = "Allow"
    actions   = ["states:*", "xray:*"]
    resources = ["*"]
  }

  # The one thing a deploy may never do.
  #
  # Widening its own trust is the move that turns a compromised workflow into a permanent
  # foothold: edit the `sub` condition, and every branch can assume the role from then on. The
  # deny is explicit because an allow list that merely omits these actions is one careless
  # `iam:*` away from including them — and `Foundation` above contains exactly that shape.
  statement {
    sid    = "NeverWidenItsOwnTrust"
    effect = "Deny"
    actions = [
      "iam:UpdateAssumeRolePolicy",
      "iam:CreateOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:DeleteOpenIDConnectProvider",
      "iam:AttachRolePolicy",
      "iam:CreateUser",
      "iam:CreateAccessKey",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy_layers" {
  #checkov:skip=CKV_AWS_111:See the note in aws_iam_policy_document.deploy_layers.
  #checkov:skip=CKV_AWS_356:As above.
  #checkov:skip=CKV_AWS_109:As above.
  #checkov:skip=CKV_AWS_107:As above.
  #checkov:skip=CKV_AWS_110:As above.
  #checkov:skip=CKV_AWS_108:As above.
  name   = "deploy-the-layers"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_layers.json
}
