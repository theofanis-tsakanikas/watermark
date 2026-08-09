# The Managed Flink application: a zip of `streaming/`, a role, and the parallelism settings
# that cannot be corrected later.
#
# The application is a thin adapter over `watermark.core` (ADR-0003), which is why this file
# configures placement and capacity and nothing semantic. Every duration the job uses comes out
# of the package; `scripts/check_adapter_is_thin.py` refuses a numeric literal in `streaming/`
# so that a window length cannot quietly move from the core into a PyFlink call.

resource "aws_s3_object" "application" {
  bucket = data.aws_s3_bucket.lakehouse.id
  key    = "applications/${var.project}-${data.archive_file.application.output_md5}.zip"
  source = data.archive_file.application.output_path
  etag   = data.archive_file.application.output_md5

  # Content-addressed. A fixed key would let two different builds share one object version, and
  # Managed Flink would then restart on whichever happened to be there — the deploy would look
  # identical and the running code would not be.
  kms_key_id = data.aws_kms_key.data.arn
}

data "archive_file" "application" {
  type        = "zip"
  output_path = "${path.module}/.build/application.zip"

  source_dir = "${path.module}/.package"
  # `.package` is assembled by `make package`, which vendors `src/watermark`, `streaming/` and
  # `contracts/` into one directory. Vendored rather than pip-installed at runtime: Managed
  # Flink has no egress in this VPC, so a job that expected to resolve a dependency at start-up
  # would hang rather than fail — the same shape as a missing VPC endpoint.
}

resource "aws_iam_role" "flink" {
  name = "${var.project}-flink"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "kinesisanalytics.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "flink" {
  # The three below apply to the `ReachTheVpc` statement only. Managed Flink attaches to a VPC
  # by creating elastic network interfaces on demand, and an interface that does not exist yet
  # cannot be named in a policy — AWS documents these actions as requiring `*`. Every other
  # statement in this document names its resources, which is the part a reader should check.
  #checkov:skip=CKV_AWS_111:ec2 network-interface actions cannot be resource-scoped; the interfaces are created by the service at attach time.
  #checkov:skip=CKV_AWS_356:As above.
  #checkov:skip=CKV_AWS_109:As above — `ec2:Describe*` is read-only and has no resource form.
  statement {
    sid    = "ReadTheStreams"
    effect = "Allow"
    actions = [
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:ListShards",
      "kinesis:SubscribeToShard",
    ]
    resources = [
      aws_kinesis_stream.meter_readings.arn,
      aws_kinesis_stream.substation_telemetry.arn,
    ]
  }

  statement {
    sid       = "ReadItsOwnCode"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${data.aws_s3_bucket.lakehouse.arn}/applications/*"]
  }

  statement {
    sid    = "WriteTheLakehouse"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      data.aws_s3_bucket.lakehouse.arn,
      "${data.aws_s3_bucket.lakehouse.arn}/warehouse/*",
      "${data.aws_s3_bucket.lakehouse.arn}/quarantine/*",
    ]
  }

  statement {
    sid       = "UseTheDataKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [data.aws_kms_key.data.arn]
  }

  statement {
    sid       = "WriteItsOwnLogs"
    effect    = "Allow"
    actions   = ["logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.flink.arn}:*"]
  }

  statement {
    sid       = "ReachTheVpc"
    effect    = "Allow"
    actions   = ["ec2:DescribeVpcs", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups", "ec2:DescribeDhcpOptions", "ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DeleteNetworkInterface", "ec2:CreateNetworkInterfacePermission"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "flink" {
  #checkov:skip=CKV_AWS_355:See the network-interface note in aws_iam_policy_document.flink.
  #checkov:skip=CKV_AWS_290:As above.
  name   = "run-the-job"
  role   = aws_iam_role.flink.id
  policy = data.aws_iam_policy_document.flink.json
}

resource "aws_cloudwatch_log_group" "flink" {
  name              = "/aws/kinesis-analytics/${var.project}"
  retention_in_days = 400
  kms_key_id        = data.aws_kms_key.logs.arn
}

resource "aws_cloudwatch_log_stream" "flink" {
  name           = "application"
  log_group_name = aws_cloudwatch_log_group.flink.name
}

resource "aws_kinesisanalyticsv2_application" "watermark" {
  name                   = var.project
  runtime_environment    = var.flink_runtime
  service_execution_role = aws_iam_role.flink.arn

  application_configuration {
    # Snapshots are Flink savepoints. Enabled because the Phase 4 recovery drill — kill the job
    # mid-window, restore, assert no double counting — is only possible if there is something
    # to restore from, and because Managed Flink takes one automatically on every update.
    application_snapshot_configuration {
      snapshots_enabled = true
    }

    application_code_configuration {
      code_content_type = "ZIPFILE"

      code_content {
        s3_content_location {
          bucket_arn = data.aws_s3_bucket.lakehouse.arn
          file_key   = aws_s3_object.application.key
        }
      }
    }

    flink_application_configuration {
      checkpoint_configuration {
        configuration_type    = "CUSTOM"
        checkpointing_enabled = true
        # A minute. Shorter costs throughput for state this size; longer means more to reprocess
        # after a restore, and the reprocessing is bounded by stream retention rather than by
        # anything cheap.
        checkpoint_interval           = 60000
        min_pause_between_checkpoints = 30000
      }

      monitoring_configuration {
        configuration_type = "CUSTOM"
        log_level          = "INFO"
        # Per-operator, not per-application. When a window stops closing the question is *which
        # operator* is behind, and application-level metrics cannot answer it.
        metrics_level = "OPERATOR"
      }

      parallelism_configuration {
        configuration_type   = "CUSTOM"
        parallelism          = var.parallelism
        parallelism_per_kpu  = var.parallelism_per_kpu
        auto_scaling_enabled = true
      }
    }

    environment_properties {
      property_group {
        property_group_id = "kinesis.analytics.flink.run.options"

        property_map = {
          python  = "streaming/job.py"
          jarfile = "lib/flink-sql-connector-kinesis.jar"
        }
      }

      property_group {
        property_group_id = "watermark"

        # Placement, never semantics. `streaming/config.py` refuses to default any of these: a
        # job with a guessed stream name starts, reads nothing, and reports healthy.
        property_map = {
          WATERMARK_REGION                     = var.aws_region
          WATERMARK_METER_STREAM               = aws_kinesis_stream.meter_readings.name
          WATERMARK_TELEMETRY_STREAM           = aws_kinesis_stream.substation_telemetry.name
          WATERMARK_OUTPUT_BUCKET              = data.aws_s3_bucket.lakehouse.id
          WATERMARK_CHECKPOINT_INTERVAL_MILLIS = "60000"
          WATERMARK_MAX_PARALLELISM            = tostring(var.max_parallelism)
        }
      }
    }

    vpc_configuration {
      subnet_ids         = data.aws_subnets.private.ids
      security_group_ids = [data.aws_security_group.endpoints.id]
    }
  }

  cloudwatch_logging_options {
    log_stream_arn = aws_cloudwatch_log_stream.flink.arn
  }

  # The application starts stopped. Starting it is a deliberate act in the deploy workflow, not
  # a side effect of `terraform apply` — the three expensive things in this system bill from the
  # moment they run, and an apply that also starts them is an apply somebody runs to check the
  # plan and then pays for.
  start_application = false
}

# Downtime is the alarm, not errors. A Flink job that is failing loudly gets noticed; one that
# has been DOWN since a deploy at 18:00 on Friday looks like a quiet weekend.
resource "aws_cloudwatch_metric_alarm" "application_down" {
  alarm_name          = "${var.project}-flink-down"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "uptime"
  namespace           = "AWS/KinesisAnalytics"
  period              = 300
  statistic           = "Maximum"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_description   = "The job is not running. Windows are not closing, and nothing else in this estate will say so."

  dimensions    = { Application = aws_kinesisanalyticsv2_application.watermark.name }
  alarm_actions = [aws_sns_topic.stream_alarms.arn]
}
