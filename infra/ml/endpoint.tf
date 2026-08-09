# The real-time endpoint, off by default.
#
# It is the third expensive thing in `CLAUDE.md` and the one with the least to show for itself
# between requests: an instance billed by the hour, idle. It exists behind a flag so that
# standing it up is a decision somebody makes, not a default somebody inherits.
#
# The shadow-to-canary-to-rollback progression and the Model Monitor schedules arrive in
# Phase 3 with the models they watch. Provisioning a monitoring schedule for a model that does
# not exist would be a green tick over work that has not happened.

resource "aws_sagemaker_endpoint_configuration" "anomaly" {
  count = var.endpoint_enabled ? 1 : 0

  name        = "${var.project}-anomaly"
  kms_key_arn = data.aws_kms_key.data.arn

  production_variants {
    variant_name           = "primary"
    model_name             = "${var.project}-anomaly-placeholder"
    initial_instance_count = 1
    instance_type          = "ml.m5.large"
    initial_variant_weight = 1
  }

  data_capture_config {
    enable_capture              = true
    initial_sampling_percentage = 100
    destination_s3_uri          = "s3://${data.aws_s3_bucket.lakehouse.id}/model-capture/anomaly"
    kms_key_id                  = data.aws_kms_key.data.arn

    # Both. AI Act Art. 12 asks for records of the inputs *as served* — capturing only the
    # output records what the model said and loses what it was asked, which is the half a
    # post-market investigation needs.
    capture_options {
      capture_mode = "Input"
    }
    capture_options {
      capture_mode = "Output"
    }
  }
}
