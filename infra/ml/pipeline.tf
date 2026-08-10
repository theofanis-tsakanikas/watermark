# The training pipeline, so that a model has a provenance rather than a story.
#
# Before this existed, `src/watermark/models/` was a library: correct, tested, and called by
# nothing. A model artefact in the registry could not answer which snapshot it read, which code
# produced it, or whether a bias analysis had been run at all — the promotion gate demanded a
# model card and a named approver, and both were hand-assembled by whoever was promoting.
#
# **AI Act Art. 12 asks for records of the process, not assertions about it.** A pipeline
# execution is that record: five steps, each with its inputs, its container image digest and
# its outputs, and a registration step that cannot run unless the four before it did.
#
# The order is the argument:
#
#   snapshot  — pin the offline store at an instant, and record the digest of what was read
#   train     — fit, from that snapshot only
#   clarify   — the industry-standard bias report, in the form an auditor expects
#   examine   — our own analysis, which measures the thing Clarify cannot see
#   register  — into the Model Registry, PendingManualApproval, never Approved
#
# `clarify` runs *and does not vote*. Measured over the two models this repository trains, its
# disparate impact moves by 4 per mille while the defect moves by 777 — see
# `src/watermark/models/clarify.py` and ADR-0006. Wiring it into the gate would refuse the
# corrected model. It produces the report; `examine` produces the finding.
#
# **`register` writes PendingManualApproval and nothing here can change that.** Doctrine 5: the
# pipeline may not approve its own output, and `registry.tf` mirrors that as an IAM deny on the
# pipeline role for `sagemaker:UpdateModelPackage`. A pipeline that could promote is a pipeline
# whose approver is decoration.

locals {
  # One image for every step. Pinned by tag here and by digest in the pipeline's execution
  # record: two runs of the same pipeline definition against a moved tag are two different
  # experiments, and only the execution record can tell you which one you are looking at.
  training_image = "683313688378.dkr.ecr.${var.aws_region}.amazonaws.com/sagemaker-xgboost:1.7-1"

  # Clarify ships as its own image, per region. Named rather than looked up, because a data
  # source that resolves to "whatever is current" reintroduces exactly the drift the digest in
  # the execution record exists to pin down.
  clarify_image = "048994720340.dkr.ecr.${var.aws_region}.amazonaws.com/sagemaker-clarify-processing:1.0"

  pipeline_root = "s3://${data.aws_s3_bucket.lakehouse.id}/pipelines/${var.project}"

  # Model Monitor's own image, per region. Same reasoning as the two above: named, not resolved.
  monitor_image = "048994720340.dkr.ecr.${var.aws_region}.amazonaws.com/sagemaker-model-monitor-analyzer:latest"

  # Small on purpose, and short-lived. Every step is a batch job over a synthetic day; the
  # instance exists for minutes and the pipeline is not left standing between runs.
  instance_type = "ml.m5.large"
}

resource "aws_sagemaker_pipeline" "training" {
  pipeline_name         = "${var.project}-anomaly-training"
  pipeline_display_name = "${var.project}-anomaly-training"
  role_arn              = aws_iam_role.pipeline.arn

  pipeline_description = join(" ", [
    "Pins a snapshot, fits the anomaly scorer, runs Clarify and this project's own bias",
    "analysis, and registers the result as PendingManualApproval. It cannot approve.",
  ])

  pipeline_definition = jsonencode({
    Version = "2020-12-01"

    Parameters = [
      # No defaults on the two that decide what the run *means*. A pipeline that will start
      # with yesterday's snapshot because nobody passed one is a pipeline that produces a
      # model nobody can trace.
      { Name = "SnapshotId", Type = "String" },
      { Name = "AsOfInstant", Type = "String" },
      { Name = "InstanceType", Type = "String", DefaultValue = local.instance_type },
    ]

    Steps = [
      {
        Name = "PinTheSnapshot"
        Type = "Processing"
        Arguments = {
          ProcessingResources = {
            ClusterConfig = {
              InstanceCount  = 1
              InstanceType   = { Get = "Parameters.InstanceType" }
              VolumeSizeInGB = 20
              # The volume holds a copy of the training set, which is personal data under the
              # same key as the lakehouse it came from.
              VolumeKmsKeyId = data.aws_kms_key.data.arn
            }
          }
          AppSpecification = {
            ImageUri = local.clarify_image
            ContainerEntrypoint = [
              "python3", "-m", "watermark.models.snapshot",
              "--snapshot", { Get = "Parameters.SnapshotId" },
              "--as-of", { Get = "Parameters.AsOfInstant" },
            ]
          }
          RoleArn = aws_iam_role.training.arn
          ProcessingOutputConfig = {
            KmsKeyId = data.aws_kms_key.data.arn
            Outputs = [{
              OutputName = "dataset"
              S3Output = {
                S3Uri        = "${local.pipeline_root}/dataset"
                LocalPath    = "/opt/ml/processing/output"
                S3UploadMode = "EndOfJob"
              }
            }]
          }
          NetworkConfig = {
            EnableNetworkIsolation                = false
            EnableInterContainerTrafficEncryption = true
            VpcConfig = {
              SecurityGroupIds = [data.aws_security_group.endpoints.id]
              Subnets          = data.aws_subnets.private.ids
            }
          }
        }
      },

      {
        Name      = "Train"
        Type      = "Training"
        DependsOn = ["PinTheSnapshot"]
        Arguments = {
          AlgorithmSpecification = {
            TrainingImage     = local.training_image
            TrainingInputMode = "File"
          }
          RoleArn = aws_iam_role.training.arn
          # Pinned, all three. ADR-0005: this model sits in the *practical* reproducibility
          # tier — the same snapshot, the same image digest and the same seed yield the same
          # metrics, which is a weaker guarantee than the deterministic model's byte-identical
          # artefact and the strongest one gradient boosting can honestly offer.
          HyperParameters = {
            objective   = "binary:logistic"
            num_round   = "50"
            max_depth   = "4"
            eta         = "0.2"
            seed        = "20260810"
            nthread     = "1"
            tree_method = "exact"
          }
          InputDataConfig = [{
            ChannelName = "train"
            DataSource = {
              S3DataSource = {
                S3DataType             = "S3Prefix"
                S3Uri                  = "${local.pipeline_root}/dataset"
                S3DataDistributionType = "FullyReplicated"
              }
            }
            ContentType = "text/csv"
          }]
          OutputDataConfig = {
            S3OutputPath = "${local.pipeline_root}/model"
            KmsKeyId     = data.aws_kms_key.data.arn
          }
          ResourceConfig = {
            InstanceCount  = 1
            InstanceType   = { Get = "Parameters.InstanceType" }
            VolumeSizeInGB = 20
            VolumeKmsKeyId = data.aws_kms_key.data.arn
          }
          # Nothing in this estate should train for an hour. A run that has not finished in
          # twenty minutes is a run that is wrong, and a stopping condition is the difference
          # between noticing that and paying for it.
          StoppingCondition                     = { MaxRuntimeInSeconds = 1200 }
          EnableInterContainerTrafficEncryption = true
          VpcConfig = {
            SecurityGroupIds = [data.aws_security_group.endpoints.id]
            Subnets          = data.aws_subnets.private.ids
          }
        }
      },

      {
        Name      = "Clarify"
        Type      = "Processing"
        DependsOn = ["Train"]
        Arguments = {
          ProcessingResources = {
            ClusterConfig = {
              InstanceCount  = 1
              InstanceType   = { Get = "Parameters.InstanceType" }
              VolumeSizeInGB = 20
              VolumeKmsKeyId = data.aws_kms_key.data.arn
            }
          }
          AppSpecification = { ImageUri = local.clarify_image }
          RoleArn          = aws_iam_role.training.arn
          ProcessingInputs = [{
            InputName = "analysis_config"
            S3Input = {
              # Generated by `watermark.models.clarify.analysis_configuration`, not committed.
              # The facet boundaries and column names have to agree with the dataset the step
              # above just wrote, and a hand-maintained copy agrees on the day it is written.
              S3Uri       = "${local.pipeline_root}/clarify/analysis_config.json"
              LocalPath   = "/opt/ml/processing/input/config"
              S3DataType  = "S3Prefix"
              S3InputMode = "File"
            }
          }]
          ProcessingOutputConfig = {
            KmsKeyId = data.aws_kms_key.data.arn
            Outputs = [{
              OutputName = "analysis"
              S3Output = {
                S3Uri        = "${local.pipeline_root}/clarify/report"
                LocalPath    = "/opt/ml/processing/output"
                S3UploadMode = "EndOfJob"
              }
            }]
          }
        }
      },

      {
        Name      = "Examine"
        Type      = "Processing"
        DependsOn = ["Clarify"]
        Arguments = {
          ProcessingResources = {
            ClusterConfig = {
              InstanceCount  = 1
              InstanceType   = { Get = "Parameters.InstanceType" }
              VolumeSizeInGB = 20
              VolumeKmsKeyId = data.aws_kms_key.data.arn
            }
          }
          AppSpecification = {
            ImageUri            = local.clarify_image
            ContainerEntrypoint = ["python3", "-m", "watermark.models.examine"]
          }
          RoleArn = aws_iam_role.training.arn
          ProcessingOutputConfig = {
            KmsKeyId = data.aws_kms_key.data.arn
            Outputs = [{
              OutputName = "model_card"
              S3Output = {
                S3Uri        = "${local.pipeline_root}/model-card"
                LocalPath    = "/opt/ml/processing/output"
                S3UploadMode = "EndOfJob"
              }
            }]
          }
        }
      },

      {
        Name      = "Register"
        Type      = "RegisterModel"
        DependsOn = ["Examine"]
        Arguments = {
          ModelPackageGroupName = aws_sagemaker_model_package_group.meter_anomaly.model_package_group_name
          InferenceSpecification = {
            Containers = [{
              Image        = local.training_image
              ModelDataUrl = "${local.pipeline_root}/model"
            }]
            SupportedContentTypes                   = ["text/csv"]
            SupportedResponseMIMETypes              = ["text/csv"]
            SupportedRealtimeInferenceInstanceTypes = [local.instance_type]
          }
          # The whole point of the step. Never "Approved": a human decides, and `registry.tf`
          # denies this role the call that would change it.
          ModelApprovalStatus = "PendingManualApproval"
          ModelMetrics = {
            Bias = {
              Report = {
                ContentType = "application/json"
                S3Uri       = "${local.pipeline_root}/clarify/report/analysis.json"
              }
            }
          }
        }
      },
    ]
  })

  tags = { "watermark:expires-at" = var.expires_at }
}
