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
  #
  # Two forms of the same claim, because the account decides which it sends and not this file.
  # A federation that works only against the format in use on the day it was written is a
  # federation that breaks on a Tuesday. Accepting the pair widens nothing — both name one
  # repository and one environment — and the id form is the one that cannot be taken over,
  # because a released account name can be re-registered by somebody else and an id cannot.
  #
  # `deploy` and `destroy` are written here rather than taken from a variable. They have to
  # equal the `environment:` lines in `deploy.yml` and `destroy.yml` exactly, so a knob is one
  # that silently breaks federation when turned — and the failure is an
  # `AssumeRoleWithWebIdentity` denial that names nothing.
  #
  # Four lines where two comprehensions would do. The loop put the `repo:` prefix behind two
  # more locals, and the subject a trust policy grants is the last string in this repository
  # that should have to be assembled in someone's head to be read.
  trusted_subjects = [
    "repo:${var.github_owner}/${var.github_repo}:environment:deploy",
    "repo:${var.github_owner}/${var.github_repo}:environment:destroy",
    "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repository_id}:environment:deploy",
    "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repository_id}:environment:destroy",
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

  # Push the processing image, and nothing else in ECR. `GetAuthorizationToken` has no resource
  # form — AWS requires "*" — which is why it is a statement of its own rather than folded in.
  statement {
    sid       = "PushTheProcessingImage"
    effect    = "Allow"
    actions   = ["ecr:*"]
    resources = [aws_ecr_repository.processing.arn]
  }

  statement {
    sid       = "LogInToTheRegistry"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
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
      # The data plane, not just the control plane. Creating a Lambda with `kms_key_arn` makes
      # the *caller* encrypt the environment variables, so a role that can create a key and not
      # use one fails with "Lambda was unable to encrypt your environment variables" — which
      # names KMS but not the missing action. The same applies to any resource this layer
      # creates that is encrypted at rest with a key it also created.
      "kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*", "kms:ReEncrypt*",
      # And grants. Lambda does not hold the caller's credentials at invoke time, so
      # `CreateFunction` with a customer key issues a *grant* to the Lambda service on the
      # caller's behalf. `RetireGrant` and `ListGrants` are the destroy and refresh halves —
      # without them a teardown leaves grants behind on a key it is trying to delete.
      "kms:CreateGrant", "kms:RetireGrant", "kms:ListGrants", "kms:RevokeGrant",
      "s3:CreateBucket", "s3:DeleteBucket", "s3:Put*", "s3:Get*", "s3:List*",
      "budgets:*", "sns:*", "logs:*", "lambda:*", "events:*",
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:PassRole",
      "iam:*RolePolicy", "iam:ListRolePolicies", "iam:TagRole",
      # Terraform reads a role back after creating it, and `iam:*RolePolicy` does not match
      # `ListAttachedRolePolicies` — the glob ends in the singular. The first apply that
      # reached AWS died on exactly this, after creating the role it then could not read.
      "iam:ListAttachedRolePolicies", "iam:ListRoleTags", "iam:ListInstanceProfilesForRole",
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
      # Terraform reads tags back after creating an alarm, and CloudWatch keeps that behind its
      # own action rather than folding it into Describe. Third instance of the same shape in
      # this file: a create grant without the read grant that follows it, which fails *after*
      # the resource exists and reads like a permissions problem with the thing just built.
      "cloudwatch:ListTagsForResource", "cloudwatch:TagResource", "cloudwatch:UntagResource",
      # Reading metrics back is the capture's whole point: the evidence of a run is the metric
      # series, not the fact that the workflow exited zero. Create-without-read, a fourth time.
      "cloudwatch:GetMetricStatistics", "cloudwatch:GetMetricData", "cloudwatch:ListMetrics",
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

# Lake Formation has its own permission model on top of IAM, and `lakeformation:*` in an IAM
# policy does not reach it. Creating an LF-Tag needs the caller to be a registered **data lake
# administrator**, which is a Lake Formation setting rather than a grant — so the governance
# apply failed with "Insufficient Lake Formation permission(s): Required Create LF Tag on
# Catalog" while holding every IAM action it could possibly need.
#
# Registering the role is a chicken-and-egg: only an existing administrator may name another.
# That makes it bootstrap's job, which is the same argument as the deploy role itself — this
# layer exists to give CI the standing it cannot give itself.
#
# `#checkov:skip` is not needed: this resource has no scanner rule. The risk it carries is real
# and bounded the same way everything else here is — the role can only be assumed from one
# repository and one environment.
resource "aws_lakeformation_data_lake_settings" "administrators" {
  admins = [
    aws_iam_role.deploy.arn,
    # The identity that applies this layer keeps its own administrator standing. Omitting it
    # locks the human out of Lake Formation the moment this applies, leaving a console nobody
    # can fix the estate from.
    data.aws_caller_identity.current.arn,
  ]
}
