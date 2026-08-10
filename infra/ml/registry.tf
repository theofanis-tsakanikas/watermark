# The Model Registry, and the gate in front of it.
#
# Claim 5 is that no model reaches an endpoint without passing performance thresholds, bias
# thresholds, a model card and a named approver. The registry is where that becomes structural
# rather than procedural: a package group whose approval status must be flipped by a human, and
# an endpoint that can only be built from an Approved package.
#
# Doctrine 5 — nothing approves itself — is why the approval permission is *not* on the
# pipeline's execution role. The pipeline can register; only a named principal can approve.

resource "aws_sagemaker_model_package_group" "curtailment_forecast" {
  model_package_group_name        = "${var.project}-curtailment-forecast"
  model_package_group_description = "Short-horizon substation load forecast. Argued high-risk under AI Act Annex III(2) — see docs/REGULATORY.md."
}

resource "aws_sagemaker_model_package_group" "meter_anomaly" {
  model_package_group_name        = "${var.project}-meter-anomaly"
  model_package_group_description = "Tampering and non-technical-loss scoring. Not Annex III; GDPR Art. 22 governs it, and the output is a ranked queue a human works — never an actuation."
}

# The pipeline may register a model version. It may not approve one.
#
# One document per group, keyed by group. A resource policy attached to one group named *both*
# group ARNs and a `model-package/watermark-*` wildcard, and SageMaker refused it: "Invalid
# Policy: The relative-id". A resource-based policy governs the resource it is attached to, and
# naming a sibling in it is not a wider grant — it is an invalid one.
data "aws_iam_policy_document" "registry_no_self_approval" {
  for_each = {
    curtailment_forecast = aws_sagemaker_model_package_group.curtailment_forecast.arn
    meter_anomaly        = aws_sagemaker_model_package_group.meter_anomaly.arn
  }

  statement {
    sid       = "TheGroupIsReadableAndWritable"
    effect    = "Allow"
    actions   = ["sagemaker:CreateModelPackage", "sagemaker:DescribeModelPackage", "sagemaker:ListModelPackages"]
    resources = [each.value]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.pipeline.arn]
    }
  }

  # Doctrine 5, as the estate's half of what `promotion.py` enforces offline. The pipeline that
  # produced the candidate cannot be the identity that approves it.
  statement {
    sid       = "NothingApprovesItself"
    effect    = "Deny"
    actions   = ["sagemaker:UpdateModelPackage"]
    resources = [each.value]
    principals {
      type = "AWS"
      identifiers = [
        aws_iam_role.pipeline.arn,
        aws_iam_role.training.arn,
      ]
    }
  }
}

resource "aws_sagemaker_model_package_group_policy" "curtailment_forecast" {
  model_package_group_name = aws_sagemaker_model_package_group.curtailment_forecast.model_package_group_name
  resource_policy          = data.aws_iam_policy_document.registry_no_self_approval["curtailment_forecast"].json
}

resource "aws_sagemaker_model_package_group_policy" "meter_anomaly" {
  model_package_group_name = aws_sagemaker_model_package_group.meter_anomaly.model_package_group_name
  resource_policy          = data.aws_iam_policy_document.registry_no_self_approval["meter_anomaly"].json
}

resource "aws_iam_role" "pipeline" {
  name = "${var.project}-pipeline"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role" "training" {
  name = "${var.project}-training"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "training" {
  statement {
    sid    = "ReadTheOfflineStoreAndWriteArtefacts"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      data.aws_s3_bucket.lakehouse.arn,
      "${data.aws_s3_bucket.lakehouse.arn}/feature-store/*",
      "${data.aws_s3_bucket.lakehouse.arn}/models/*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [data.aws_kms_key.data.arn]
  }

  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/sagemaker/*"]
  }

  statement {
    sid       = "PublishItsOwnMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["/aws/sagemaker/TrainingJobs", var.project]
    }
  }
}

resource "aws_iam_role_policy" "training" {
  name   = "train-from-the-offline-store"
  role   = aws_iam_role.training.id
  policy = data.aws_iam_policy_document.training.json
}

resource "aws_iam_role_policy" "pipeline" {
  name = "orchestrate-training"
  role = aws_iam_role.pipeline.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunTheSteps"
        Effect = "Allow"
        Action = [
          "sagemaker:CreateTrainingJob",
          "sagemaker:DescribeTrainingJob",
          "sagemaker:CreateProcessingJob",
          "sagemaker:DescribeProcessingJob",
          "sagemaker:CreateModelPackage",
          "sagemaker:DescribeModelPackage",
        ]
        Resource = "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*/${var.project}-*"
      },
      {
        Sid      = "HandTheTrainingRoleToTheJob"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = aws_iam_role.training.arn
        Condition = {
          StringEquals = { "iam:PassedToService" = "sagemaker.amazonaws.com" }
        }
      },
    ]
  })
}
