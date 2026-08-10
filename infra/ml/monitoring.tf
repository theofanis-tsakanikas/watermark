# The live endpoint, and what watches it.
#
# `endpoint_enabled` used to default to false and stay there, on the grounds that an endpoint
# costs money while idle. That is true and it was the wrong conclusion: a system that scores
# meters for inspection and has never served a request is a design, not a platform, and Model
# Monitor has nothing to monitor without one.
#
# So the endpoint is real, and the *lifetime* is what is bounded rather than the capability.
# `capture.yml` stands it up, drives the scenario through it, captures the evidence and tears
# it down in one workflow; `watermark:expires-at` and the reaper are the backstop for the run
# that dies between the third step and the fourth.
#
# ── What monitoring here is, and what it is not ─────────────────────────────────────────────
#
# **Drift is not staleness, and this project already handles the second one.** Claim 4 refuses
# to serve a feature past its freshness budget: that is a statement about *when* a value was
# computed, checked before every decision, in the deterministic path. Model Monitor asks a
# different question — whether the values arriving now look like the values the model was
# fitted on. A feature can be perfectly fresh and completely unlike anything in the training
# set, and nothing in claims 1 to 7 would notice.
#
# The two failures need different responses, which is why they are not merged. A stale feature
# has a deterministic answer: fall back, mark it, carry the marker. Drift does not — the right
# response is to retrain, or to stop trusting the model, and both are decisions a person takes.
# So monitoring **reports**; it does not actuate. There is no auto-rollback here, deliberately.

resource "aws_sagemaker_model" "anomaly" {
  count = var.endpoint_enabled ? 1 : 0

  name                     = "${var.project}-anomaly"
  execution_role_arn       = aws_iam_role.training.arn
  enable_network_isolation = true

  primary_container {
    image = local.training_image
    # The artefact a *human* approved. `promoted_model_name` has no default and the endpoint
    # cannot be enabled without it — an endpoint pointed at "the latest model" is one that
    # silently starts serving whatever the pipeline produced last night, which is the shape of
    # doctrine 5 being lost without anybody editing a policy.
    model_data_url = "${local.pipeline_root}/approved/${var.promoted_model_name}/model.tar.gz"
  }

  vpc_config {
    security_group_ids = [data.aws_security_group.endpoints.id]
    subnets            = data.aws_subnets.private.ids
  }

  tags = { "watermark:expires-at" = var.expires_at }
}

resource "aws_sagemaker_endpoint" "anomaly" {
  count = var.endpoint_enabled ? 1 : 0

  name                 = "${var.project}-anomaly"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.anomaly[0].name

  # The tag is not decoration on this resource. It is the most expensive thing in the estate
  # per hour, and the reaper reads exactly this.
  tags = { "watermark:expires-at" = var.expires_at }
}

# ── The baseline ────────────────────────────────────────────────────────────────────────────
#
# Monitoring compares production traffic against a *baseline* — statistics and constraints
# computed from the training set. Without one there is nothing to be different from, and a
# monitoring schedule with no baseline runs, succeeds, and reports nothing wrong for ever.
#
# The baseline is produced by the pipeline's `Examine` step from the same pinned snapshot the
# model was fitted on. Not from "recent production data", which is the tempting shortcut and
# the one that defines drift as normal.

resource "aws_sagemaker_data_quality_job_definition" "anomaly" {
  count = var.endpoint_enabled ? 1 : 0

  name     = "${var.project}-anomaly-data-quality"
  role_arn = aws_iam_role.training.arn

  data_quality_app_specification {
    image_uri = local.monitor_image
  }

  data_quality_baseline_config {
    constraints_resource {
      s3_uri = "${local.pipeline_root}/analysis/baseline/constraints.json"
    }
    statistics_resource {
      s3_uri = "${local.pipeline_root}/analysis/baseline/statistics.json"
    }
  }

  data_quality_job_input {
    endpoint_input {
      endpoint_name = aws_sagemaker_endpoint.anomaly[0].name
      # Every request. The endpoint is up for the length of a capture, so a sample would be a
      # sample of an already small population — and the point of the run is evidence.
      local_path = "/opt/ml/processing/input/endpoint"
    }
  }

  data_quality_job_output_config {
    kms_key_id = data.aws_kms_key.data.arn
    monitoring_outputs {
      s3_output {
        s3_uri     = "${local.pipeline_root}/monitoring/data-quality"
        local_path = "/opt/ml/processing/output"
      }
    }
  }

  job_resources {
    cluster_config {
      instance_count    = 1
      instance_type     = local.instance_type
      volume_size_in_gb = 20
      volume_kms_key_id = data.aws_kms_key.data.arn
    }
  }

  network_config {
    enable_inter_container_traffic_encryption = true
    vpc_config {
      security_group_ids = [data.aws_security_group.endpoints.id]
      subnets            = data.aws_subnets.private.ids
    }
  }

  stopping_condition {
    max_runtime_in_seconds = 1800
  }

  tags = { "watermark:expires-at" = var.expires_at }
}

resource "aws_sagemaker_monitoring_schedule" "anomaly" {
  count = var.endpoint_enabled ? 1 : 0

  name = "${var.project}-anomaly-data-quality"

  monitoring_schedule_config {
    monitoring_job_definition_name = aws_sagemaker_data_quality_job_definition.anomaly[0].name
    monitoring_type                = "DataQuality"

    schedule_config {
      # Hourly is the finest SageMaker offers, and it is the right one here for a reason that
      # is about the capture rather than about production: a schedule that fires daily would
      # never fire at all inside a bounded run, and a monitoring configuration nobody has seen
      # execute is a configuration nobody knows is wrong.
      schedule_expression = "cron(0 * ? * * *)"
    }
  }

  tags = { "watermark:expires-at" = var.expires_at }
}
