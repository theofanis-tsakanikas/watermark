# Two mechanisms, and they fail in opposite directions on purpose.
#
# **The budget action** is the ceiling: at the threshold it attaches a deny to the deploy role,
# so nothing more can be created. It does not warn. A warning at 80% of a €100 budget is an
# email somebody reads on Monday.
#
# It is **not in this file**, and that is the point. It lives in `infra/bootstrap/cost.tf`,
# applied before any layer can be deployed. A ceiling created by the same apply that creates the
# spending does not exist while the spending starts, and a foundation apply that fails halfway
# leaves resources standing with nothing watching them. This file used to hold the budget and
# not the action — a warning email dressed as a control, with the comment above describing
# something no resource in the repository built.
#
# **The reaper** is the floor: it destroys anything whose `watermark:expires-at` has passed,
# whether or not it is costing much. The expensive resources in this system — Managed Flink
# KPUs, the Feature Store online store, a real-time endpoint — cost money for exactly as long as
# they exist, and nothing about a forgotten one looks wrong.
#
# Neither has fired. The estate has been applied, driven and destroyed since this was written,
# and the exercise's tagged spend was USD 12.35 against a USD 110 ceiling — so the budget action
# was never close, and every resource was destroyed by the workflow well inside its
# `expires-at`. That is an untested control rather than a working one, and it is worth saying
# plainly: the ceiling and the floor are both still theoretical.

# ── The reaper ───────────────────────────────────────────────────────────────

resource "aws_iam_role" "reaper" {
  name = "${var.project}-reaper"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

#checkov:skip=CKV_AWS_111:The reaper's whole job is to destroy tagged resources it did not create; it cannot name them in advance. It is constrained by the tag condition below rather than by resource ARNs, which is the only constraint available for a sweeper.
#checkov:skip=CKV_AWS_356:As above.
data "aws_iam_policy_document" "reaper" {
  statement {
    sid    = "FindExpiredResources"
    effect = "Allow"
    actions = [
      "tag:GetResources",
      "kinesisanalyticsv2:ListApplications",
      "kinesis:ListStreams",
      "sagemaker:ListEndpoints",
      "sagemaker:ListFeatureGroups",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "DestroyThemOnlyIfTheyHaveExpired"
    effect = "Allow"
    actions = [
      "kinesisanalyticsv2:DeleteApplication",
      "kinesisanalyticsv2:StopApplication",
      "sagemaker:DeleteEndpoint",
      "sagemaker:DeleteFeatureGroup",
      "kinesis:DeleteStream",
    ]
    resources = ["*"]

    # The condition is the control. Without it this role can delete anything of these types in
    # the account; with it, only what this project tagged.
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/watermark:project"
      values   = [var.project]
    }
  }

  # The dead-letter queue the function declares. Lambda validates this at *create* time — it
  # checks the execution role can publish before it will accept the function — so a missing
  # grant is not a runtime surprise but a create failure reading "The provided execution role
  # does not have permissions to call Publish on SNS". The topic was wired as a DLQ and the
  # role was never given the one action that makes a DLQ work.
  statement {
    sid       = "ReportItsOwnFailures"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.reaper_failures.arn]
  }

  statement {
    sid       = "WriteItsOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.reaper.arn}:*"]
  }
}

resource "aws_iam_role_policy" "reaper" {
  name   = "reap-expired"
  role   = aws_iam_role.reaper.id
  policy = data.aws_iam_policy_document.reaper.json
}

# Same race as `flow_logs` in network.tf: a reference to the key is not a reference to the key
# *policy*, and until the policy is attached the logs service cannot use it.
resource "aws_cloudwatch_log_group" "reaper" {
  depends_on = [aws_kms_key_policy.logs]

  name              = "/aws/lambda/${var.project}-reaper"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

data "archive_file" "reaper" {
  type        = "zip"
  source_dir  = "${path.module}/reaper"
  output_path = "${path.module}/.build/reaper.zip"
}

resource "aws_lambda_function" "reaper" {
  #checkov:skip=CKV_AWS_117:The reaper reaches control-plane APIs only and reads nothing from inside the VPC. Placing it there would need an interface endpoint for every service it can delete — widening the network to gain nothing, since the resources it acts on are not in the VPC either.
  #checkov:skip=CKV_AWS_272:Code signing is a supply-chain control for a payload built elsewhere. This one is a hundred lines built by archive_file from a directory in this repository, and its digest is in the plan the reviewer approves.

  function_name    = "${var.project}-reaper"
  role             = aws_iam_role.reaper.arn
  handler          = "reap.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.reaper.output_path
  source_code_hash = data.archive_file.reaper.output_base64sha256
  timeout          = 300
  kms_key_arn      = aws_kms_key.logs.arn

  # One at a time. The reaper deletes things; two concurrent sweeps racing over the same
  # resource list produce a second delete against something already gone, and the error looks
  # like the reaper failing when it succeeded. It runs hourly and takes seconds.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      WATERMARK_PROJECT = var.project

      # `destroy`, explicitly. The function defaults to `report` when this is absent, and it
      # spent most of this project's life doing exactly that without anybody choosing it: it
      # classified every expired resource, logged `would delete`, and returned the list. The
      # schedule fired hourly and the log filled with lines that read like work.
      #
      # The default in the code stays `report` because the two mistakes are not symmetric. A
      # deployment that loses this variable under-deletes, which costs money; the other default
      # over-deletes, and there is no undo for that.
      WATERMARK_REAPER_MODE = "destroy"
    }
  }

  dead_letter_config {
    target_arn = aws_sns_topic.reaper_failures.arn
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.reaper]
}

resource "aws_sns_topic" "reaper_failures" {
  depends_on = [aws_kms_key_policy.logs]

  name              = "${var.project}-reaper-failures"
  kms_master_key_id = aws_kms_key.logs.arn
}

resource "aws_cloudwatch_event_rule" "reaper" {
  name                = "${var.project}-reaper"
  description         = "Destroy anything whose watermark:expires-at has passed"
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "reaper" {
  rule = aws_cloudwatch_event_rule.reaper.name
  arn  = aws_lambda_function.reaper.arn
}

resource "aws_lambda_permission" "reaper" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reaper.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reaper.arn
}

# ── The budget action, and the trap in the obvious version ───────────────────
#
# `CLAUDE.md` says an AWS Budget action disables the deploy role at its threshold. **It did not
# exist.** Not a stub, not a disabled resource — the sentence described a control the estate had
# never had, which is the same failure as the reaper's and arrived the same way: the reasoning
# was written down, and nothing was checking that the reasoning had been implemented.
#
# **The obvious implementation is a trap and it is worth naming.** Attaching a blanket deny to
# the deploy role at the threshold locks out `destroy.yml` as well as `deploy.yml` — so the
# moment spending crosses the line, the estate becomes impossible to tear down and bills for
# every hour somebody spends unpicking the policy by hand. The control designed to stop the
# spending would be the thing guaranteeing it continues.
#
# So the policy denies *creation* and leaves deletion alone. Past the threshold this account can
# still be emptied and cannot be filled, which is the shape the control was actually for.

# Budgets publishes to SNS as a service principal, so IAM on the *subscriber* is not enough:
# the topic has to accept it, and the key it is encrypted with has to let it encrypt. Both are
# below. Without either, `terraform apply` succeeds, the budget exists, the threshold is crossed
# and nothing is ever delivered — a control that fails in exactly the silence it was built for.
resource "aws_sns_topic_policy" "budget_notifications" {
  arn = aws_sns_topic.reaper_failures.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountOwnerAdministers"
        Effect    = "Allow"
        Principal = { AWS = data.aws_caller_identity.current.account_id }
        Action    = ["SNS:Publish", "SNS:Subscribe", "SNS:GetTopicAttributes"]
        Resource  = aws_sns_topic.reaper_failures.arn
      },
      {
        Sid       = "BudgetsMayNotify"
        Effect    = "Allow"
        Principal = { Service = "budgets.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.reaper_failures.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      },
    ]
  })
}

resource "aws_budgets_budget" "estate" {
  name         = "${var.project}-estate"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # `format`, not interpolation. AWS wants `user:<key>$<value>` and `$${` in HCL is the escape
  # for a literal `${` — so the obvious spelling produces the string `user:watermark:project`
  # followed by the four characters of a variable reference, and a budget filtered on a tag
  # nothing carries is a budget over an empty account that never fires.
  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:watermark:project$%s", var.project)]
  }

  # Two notifications before the action, because a control that only speaks when it acts gives
  # nobody the chance to decide. Forecast first: it is the one that arrives while there is still
  # something to do about it.
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.reaper_failures.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.reaper_failures.arn]
  }
}

data "aws_iam_policy_document" "over_budget" {
  # Deny what creates and what costs. `Delete*`, `Stop*` and `Destroy*` are deliberately absent:
  # see the note above. Read actions stay too — an account nobody can describe is an account
  # nobody can tear down either.
  statement {
    sid    = "NothingNewPastTheThreshold"
    effect = "Deny"
    actions = [
      "kinesisanalyticsv2:CreateApplication",
      "kinesisanalyticsv2:StartApplication",
      "kinesisanalyticsv2:UpdateApplication",
      "sagemaker:CreateEndpoint",
      "sagemaker:CreateEndpointConfig",
      "sagemaker:UpdateEndpoint",
      "sagemaker:CreateFeatureGroup",
      "sagemaker:CreateTrainingJob",
      "sagemaker:CreateProcessingJob",
      "kinesis:CreateStream",
      "kinesis:IncreaseStreamRetentionPeriod",
      "kinesis:UpdateShardCount",
      "glue:StartJobRun",
      "glue:CreateJob",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "over_budget" {
  # **Gated, and the gate is a laptop.** A managed policy needs `iam:CreatePolicy`, which the
  # deploy role does not hold — the glob it has ends in the singular and reaches only the
  # inline-policy calls. The permission is now written into `infra/bootstrap/oidc.tf`, and that
  # layer is the one thing in this repository that applies from a laptop rather than from a
  # gated workflow, so it takes effect on the next bootstrap apply and not before.
  #
  # Until then this is off rather than broken: the budget and both notifications still exist and
  # still fire, so the estate is watched. What is missing is the *automatic* freeze. That gap is
  # WV-004 in `contracts/waivers.yaml`, with a date on it, because a control that is described
  # and switched off is the exact failure the register exists to make impossible to forget.
  count = var.budget_action_enabled ? 1 : 0

  name        = "${var.project}-over-budget"
  description = "Attached by the budget action at its threshold. Denies creation, never deletion."
  policy      = data.aws_iam_policy_document.over_budget.json
}

resource "aws_iam_role" "budget_action" {
  count = var.budget_action_enabled ? 1 : 0

  name = "${var.project}-budget-action"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "budget_action" {
  statement {
    sid       = "AttachTheDenyPolicy"
    effect    = "Allow"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-deploy"]
  }
}

resource "aws_iam_role_policy" "budget_action" {
  count = var.budget_action_enabled ? 1 : 0

  name   = "attach-the-deny-policy"
  role   = aws_iam_role.budget_action[0].id
  policy = data.aws_iam_policy_document.budget_action.json
}

resource "aws_budgets_budget_action" "freeze" {
  count = var.budget_action_enabled ? 1 : 0

  budget_name        = aws_budgets_budget.estate.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action[0].arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.over_budget[0].arn
      roles      = ["${var.project}-deploy"]
    }
  }

  subscriber {
    address           = aws_sns_topic.reaper_failures.arn
    subscription_type = "SNS"
  }
}
