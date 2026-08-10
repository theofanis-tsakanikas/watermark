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

# Doctrine 5, on the identities rather than on the group.
#
# It belonged in the group's resource policy and cannot live there: SageMaker accepts only
# `Allow` in a model package group policy — "Only the Allow effect is supported, invalid effect
# for statement id NothingApprovesItself". A control that cannot be expressed where you first
# reach for it is not a control you drop; it is one you attach to the identity instead, which
# is where an explicit deny cannot be out-voted by any later grant.
#
# `promotion.py` enforces the same rule offline and refuses `pipeline` and `watermark-training`
# by name. This is the estate's half of that promise.
resource "aws_iam_role_policy" "no_self_approval" {
  for_each = {
    pipeline = aws_iam_role.pipeline.id
    training = aws_iam_role.training.id
  }

  name = "nothing-approves-itself"
  role = each.value

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "NothingApprovesItself"
      Effect   = "Deny"
      Action   = ["sagemaker:UpdateModelPackage"]
      Resource = "*"
    }]
  })
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

}

# IAM propagates, and SageMaker validates the principal before it has.
#
# The resource policy names `watermark-pipeline`, created moments earlier in the same apply, and
# SageMaker answered "Invalid Policy: Invalid Principal in Policy" on a fresh estate — the role
# existed and the service could not see it yet. `depends_on` does not help: Terraform had
# already ordered them correctly, and the gap is on AWS's side.
#
# Twenty seconds is the documented shape of the fix rather than a guess at a duration. It costs
# twenty seconds on a deploy that takes minutes, and it is the difference between an apply that
# works on a redeploy and one that works the first time somebody stands the estate up.
resource "time_sleep" "iam_propagation" {
  depends_on      = [aws_iam_role.pipeline, aws_iam_role.training]
  create_duration = "20s"
}

resource "aws_sagemaker_model_package_group_policy" "curtailment_forecast" {
  depends_on = [time_sleep.iam_propagation]

  model_package_group_name = aws_sagemaker_model_package_group.curtailment_forecast.model_package_group_name
  resource_policy          = data.aws_iam_policy_document.registry_no_self_approval["curtailment_forecast"].json
}

resource "aws_sagemaker_model_package_group_policy" "meter_anomaly" {
  depends_on = [time_sleep.iam_propagation]

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
      # Everything the pipeline reads and writes: its own code archive, the dataset the
      # snapshot step pins, the fitted model, and the analysis and baseline `examine` emits.
      # The prefix was added when the pipeline was, and this grant was not — so the first step
      # could reach S3 and not read the wheel that contains the module it was told to run.
      "${data.aws_s3_bucket.lakehouse.arn}/pipelines/*",
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

# A job that runs inside the VPC attaches its own network interface, using the *execution*
# role's credentials — so a training role without EC2 networking cannot start at all, and the
# error names ec2 while everything about the configuration looks like SageMaker. This is the
# documented set for VPC-attached training and processing jobs; the Describe calls have no
# resource form, which is why they sit on `*`.
# Pull the processing image. SageMaker pulls it *as the execution role*, so the role needs
# these three by name — and the error said which, which is rarer than it should be.
resource "aws_iam_role_policy" "training_ecr" {
  name = "pull-the-processing-image"
  role = aws_iam_role.training.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "PullTheProcessingImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = "arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/${var.project}/processing"
      },
      {
        # No resource form: it returns a token, not access to anything.
        Sid      = "LogInToTheRegistry"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "training_kms" {
  #checkov:skip=CKV_AWS_111:Scoped to one key ARN, which is the constraint available here.
  name = "use-the-data-key"
  role = aws_iam_role.training.id

  # A processing job encrypts its own volume and its own outputs with this key, using the
  # execution role. `Decrypt` alone was granted, which is enough to read the inputs and not
  # enough to start — "Access denied to KMS Key", naming the key rather than the direction.
  # `CreateGrant` is how the job hands the key to the instance it runs on.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "UseTheDataKey"
      Effect = "Allow"
      Action = [
        "kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*",
        "kms:GenerateDataKey*", "kms:DescribeKey", "kms:CreateGrant",
      ]
      Resource = data.aws_kms_key.data.arn
    }]
  })
}

resource "aws_iam_role_policy" "training_vpc" {
  #checkov:skip=CKV_AWS_289:`CreateNetworkInterfacePermission` is read as permissions management. It grants an ENI to a SageMaker-owned account so the job can use it, which is what a VPC-attached job is; the action has no resource form and AWS requires "*".
  #checkov:skip=CKV_AWS_290:ec2:Describe* and CreateNetworkInterface have no resource form; AWS requires "*". The interface is created into subnets this project owns, and the role is assumable only by SageMaker.
  #checkov:skip=CKV_AWS_355:As above.
  name = "attach-to-the-vpc"
  role = aws_iam_role.training.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AttachToTheVpc"
      Effect = "Allow"
      Action = [
        "ec2:CreateNetworkInterface",
        "ec2:CreateNetworkInterfacePermission",
        "ec2:DeleteNetworkInterface",
        "ec2:DeleteNetworkInterfacePermission",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeVpcs",
        "ec2:DescribeDhcpOptions",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
      ]
      Resource = "*"
    }]
  })
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
          # The Register step creates the group if it is absent, and asks for the permission
          # whether or not it is — so a role that may only register into an existing group
          # cannot register at all. `Describe` is the read that follows, as everywhere else.
          "sagemaker:CreateModelPackageGroup",
          "sagemaker:DescribeModelPackageGroup",
          "sagemaker:StopProcessingJob",
          "sagemaker:StopTrainingJob",
          # SageMaker tags every job it creates on the caller's behalf, so a role that can
          # create a job and not tag it cannot create one at all. The first pipeline execution
          # died here: "not authorized to perform: sagemaker:AddTags".
          "sagemaker:AddTags",
          "sagemaker:ListTags",
        ]
        # Two patterns, because **SageMaker names these jobs, not us**. A pipeline step's job is
        # `pipelines-<execution-id>-<StepName>-<suffix>`, so a scope of `watermark-*` matched
        # nothing the pipeline ever creates — the grant would have been correct and inert.
        Resource = [
          "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*/${var.project}-*",
          "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*/pipelines-*",
        ]
      },
      {
        # CreateModelPackage validates the artefact by reading it, as the *pipeline* role —
        # which until now held SageMaker actions and PassRole and nothing else. The failure is
        # "Access denied for bucket ... model.tar.gz", which reads like a missing object.
        Sid    = "ReadTheArtefactItIsRegistering"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          data.aws_s3_bucket.lakehouse.arn,
          "${data.aws_s3_bucket.lakehouse.arn}/pipelines/*",
        ]
      },
      {
        # And the key it is encrypted with, for the same read.
        Sid      = "DecryptTheArtefact"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = data.aws_kms_key.data.arn
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
