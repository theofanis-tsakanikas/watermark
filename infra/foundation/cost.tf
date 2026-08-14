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
