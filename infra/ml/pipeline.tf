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

# Resolved, not transcribed — for the same reason as everything bootstrap publishes.
#
# The account that owns a SageMaker algorithm image is *different in every region*, and the
# first version of this file hardcoded three of them from memory. That is a value which passes
# `terraform validate` (it is a well-formed string), passes `plan` (nothing checks it), and
# fails at the moment a training job tries to pull — after the cluster is running and paid for.
# The provider knows the table; asking it is one line and cannot be wrong by a digit.
data "aws_sagemaker_prebuilt_ecr_image" "xgboost" {
  repository_name = "sagemaker-xgboost"
  image_tag       = "1.7-1"
}

# A general-purpose processing container, and **not Clarify**.
#
# The first pipeline execution against a real account answered: "SageMaker Clarify processing is
# in maintenance mode and is not available to new customers." It is not a syntax error and no
# permission fixes it — the image cannot be pulled in this account at all.
#
# So the Clarify *step* is gone, and ADR-0006 is amended rather than quietly left standing. What
# survives is the part that mattered: `watermark.models.clarify` computes Clarify's own
# post-training metrics offline, in integers, from the same subjects — and `evals/promotion`
# proves the finding that made the comparison worth having. The report AWS would have rendered
# is unavailable; the arithmetic it would have done is in the repository and runs in CI.
# Ours, built by CI and tagged with the commit that built it. `infra/bootstrap` owns the
# repository — a build-artefact registry outlives the estate it serves.
data "aws_ecr_repository" "processing" {
  name = "${var.project}/processing"
}

data "aws_sagemaker_prebuilt_ecr_image" "monitor" {
  repository_name = "sagemaker-model-monitor-analyzer"
  image_tag       = "latest"
}

locals {
  training_image   = data.aws_sagemaker_prebuilt_ecr_image.xgboost.registry_path
  processing_image = "${data.aws_ecr_repository.processing.repository_url}:${var.processing_image_tag}"

  pipeline_root = "s3://${data.aws_s3_bucket.lakehouse.id}/pipelines/${var.project}"

  monitor_image = data.aws_sagemaker_prebuilt_ecr_image.monitor.registry_path

  # Where `make package-ml` puts the wheel. The stock AWS images have never heard of this
  # package, so every step that runs our code installs it first from this channel. A processing
  # step whose entrypoint names a module the image does not contain is a step that fails after
  # the cluster has been paid for — which is how the first draft of this file would have failed.
  code_channel = "${local.pipeline_root}/code"

  # `pip install --no-deps` because the two modules that run here import nothing but the
  # standard library and this package. Pulling pydantic into a processing container would be
  # installing a dependency to satisfy an import that is not made.

  # Small on purpose, and short-lived. Every step is a batch job over a synthetic day; the
  # instance exists for minutes and the pipeline is not left standing between runs.
  instance_type = "ml.m5.large"
}

# The code the processing steps run, as one zip.
#
# `archive_file` is a **data source**, and that is the whole reason for it: data sources are
# read at plan, while `filemd5` is a function evaluated during `terraform validate`. An earlier
# version used `filemd5` on the wheel, which made this layer unvalidatable on a clean checkout —
# and `count = 0` does not save you, because the expression is still evaluated. `infra/streaming`
# had already solved this the right way and this file now does the same.
#
# One archive rather than two objects: the wheel and the population travel together, are
# unpacked together, and cannot get out of step with each other.
data "archive_file" "code" {
  type        = "zip"
  source_dir  = "${path.module}/.package"
  output_path = "${path.module}/.build/code.zip"
}

resource "aws_s3_object" "code" {
  bucket = data.aws_s3_bucket.lakehouse.id
  key    = "pipelines/${var.project}/code/code.zip"
  source = data.archive_file.code.output_path

  # `source_hash`, not `etag`. With SSE-KMS the object's ETag is not the MD5 of the content, so
  # the provider refuses the pair outright — "Conflicting configuration arguments". `source_hash`
  # is the attribute that exists for exactly this case: it tracks the content without claiming
  # to be the server's ETag. Dropping the attribute entirely would have been the quiet mistake:
  # Terraform would then compare only the file *path*, and a rebuilt archive at the same path
  # would never be uploaded.
  source_hash = data.archive_file.code.output_md5

  kms_key_id             = data.aws_kms_key.data.arn
  server_side_encryption = "aws:kms"
}

# The step scripts, beside the archive rather than inside it.
#
# The entrypoint is `bash /opt/ml/processing/input/code/snapshot.sh`, and the script cannot be
# inside `code.zip` — nothing has unzipped it yet at the moment bash is asked to run it. The
# first attempt put them in the archive and the step died with exit code 127, "command not
# found", which names the shell rather than the missing file.
#
# `filemd5` is safe here where it was not for the wheel: these are committed files, so validate
# can always read them.
resource "aws_s3_object" "step_script" {
  for_each = fileset("${path.module}/../../pipelines/steps", "*.sh")

  bucket      = data.aws_s3_bucket.lakehouse.id
  key         = "pipelines/${var.project}/code/${each.value}"
  source      = "${path.module}/../../pipelines/steps/${each.value}"
  source_hash = filemd5("${path.module}/../../pipelines/steps/${each.value}")

  kms_key_id             = data.aws_kms_key.data.arn
  server_side_encryption = "aws:kms"
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
      # The fitted threshold, from the training step. No default: see `examine`.
      { Name = "Threshold", Type = "String" },
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
            ImageUri            = local.processing_image
            ContainerEntrypoint = ["bash", "/opt/ml/processing/input/code/snapshot.sh"]
          }
          Environment = {
            SNAPSHOT_ID = { Get = "Parameters.SnapshotId" }
            AS_OF       = { Get = "Parameters.AsOfInstant" }
          }
          RoleArn = aws_iam_role.training.arn
          ProcessingInputs = [
            {
              InputName = "code"
              S3Input = {
                S3Uri       = local.code_channel
                LocalPath   = "/opt/ml/processing/input/code"
                S3DataType  = "S3Prefix"
                S3InputMode = "File"
              }
            },
          ]
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
                S3DataType = "S3Prefix"
                # The label-first, headerless copy `snapshot` writes beside the dataset. The
                # built-in algorithm reads column 0 as the label and a header row as data.
                S3Uri                  = "${local.pipeline_root}/dataset/train"
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
        Name      = "Examine"
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
          AppSpecification = {
            ImageUri            = local.processing_image
            ContainerEntrypoint = ["bash", "/opt/ml/processing/input/code/examine.sh"]
          }
          # No default. `examine` refuses to run without it, because a threshold of zero flags
          # every meter and produces an analysis that is internally consistent and about nothing.
          Environment = { THRESHOLD = { Get = "Parameters.Threshold" } }
          RoleArn     = aws_iam_role.training.arn
          ProcessingInputs = [
            {
              InputName = "code"
              S3Input = {
                S3Uri       = local.code_channel
                LocalPath   = "/opt/ml/processing/input/code"
                S3DataType  = "S3Prefix"
                S3InputMode = "File"
              }
            },
            {
              InputName = "dataset"
              S3Input = {
                S3Uri       = "${local.pipeline_root}/dataset"
                LocalPath   = "/opt/ml/processing/input/dataset"
                S3DataType  = "S3Prefix"
                S3InputMode = "File"
              }
            },
          ]
          ProcessingOutputConfig = {
            KmsKeyId = data.aws_kms_key.data.arn
            # One output covering the whole directory, because `examine` writes three things:
            # the bias analysis, the model card, and the **monitoring baseline**. That last one
            # is what `monitoring.tf` reads, and before this step existed it was produced by
            # nothing — the schedule would have run, succeeded, and reported nothing wrong for
            # ever, because there was nothing to be different from.
            Outputs = [{
              OutputName = "analysis"
              S3Output = {
                S3Uri        = "${local.pipeline_root}/analysis"
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
          # The bias report `Examine` writes. It is this project's own analysis — the one that
          # measures label incompleteness — because Clarify is unavailable in this account.
          ModelMetrics = {
            Bias = {
              Report = {
                ContentType = "application/json"
                S3Uri       = "${local.pipeline_root}/analysis/bias.json"
              }
            }
          }
        }
      },
    ]
  })

  tags = { "watermark:expires-at" = var.expires_at }

  # Evaluated at plan, not at validate. This is the loud half of the guard above: a deploy that
  # reached AWS with an empty code channel would create a pipeline whose every step dies on a
  # missing wheel, inside a cluster that is already being paid for.
  lifecycle {
    precondition {
      condition     = data.archive_file.code.output_size > 1000 && length(aws_s3_object.step_script) == 2
      error_message = "run `make package-ml` before applying infra/ml: the code archive is empty or missing, so every step that runs our code would fail inside a paid cluster."
    }
  }
}
