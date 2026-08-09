# Two streams, both provisioned, both encrypted with the data key.
#
# The partition key is `meter_id` — 250,000 values, evenly distributed by hash. Partitioning by
# `substation_id` would put a large meter population behind 400 keys and run straight into the
# per-key ceiling that on-demand explicitly does not fix. The consequence is that per-substation
# aggregation happens inside Flink as a keyed operation, which is where it belongs anyway.

resource "aws_kinesis_stream" "meter_readings" {
  name             = "${var.project}-meter-readings"
  shard_count      = var.meter_shards
  retention_period = var.retention_hours

  encryption_type = "KMS"
  kms_key_id      = data.aws_kms_key.data.arn

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  shard_level_metrics = [
    "IncomingBytes",
    "IncomingRecords",
    "WriteProvisionedThroughputExceeded",
    "ReadProvisionedThroughputExceeded",
    "IteratorAgeMilliseconds",
  ]
}

resource "aws_kinesis_stream" "substation_telemetry" {
  name             = "${var.project}-substation-telemetry"
  shard_count      = var.telemetry_shards
  retention_period = var.retention_hours

  encryption_type = "KMS"
  kms_key_id      = data.aws_kms_key.data.arn

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  shard_level_metrics = ["IncomingRecords", "IteratorAgeMilliseconds"]
}

# The alarm that matters, and it is not throughput.
#
# `IteratorAgeMilliseconds` is how far behind the consumer is. Throughput exceptions are loud
# and self-announcing; a consumer falling steadily behind is silent, and it is the shape claim 1
# cares about — a job that is keeping up publishes windows, and a job an hour behind publishes
# windows an hour late while every dashboard stays green.
resource "aws_cloudwatch_metric_alarm" "consumer_falling_behind" {
  for_each = {
    meter     = aws_kinesis_stream.meter_readings.name
    telemetry = aws_kinesis_stream.substation_telemetry.name
  }

  alarm_name          = "${var.project}-${each.key}-iterator-age"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "GetRecords.IteratorAgeMilliseconds"
  namespace           = "AWS/Kinesis"
  period              = 60
  statistic           = "Maximum"
  # Two metering intervals. Beyond that the job is behind by more than a window, which means a
  # curtailment decision would be taken on data older than the decision's own horizon.
  threshold         = 1800000
  alarm_description = "The consumer is more than two metering intervals behind. Windows are still closing; they are closing late, which is the failure that does not announce itself."

  dimensions = { StreamName = each.value }
  alarm_actions = [aws_sns_topic.stream_alarms.arn]
  ok_actions    = [aws_sns_topic.stream_alarms.arn]
}

resource "aws_sns_topic" "stream_alarms" {
  name              = "${var.project}-stream-alarms"
  kms_master_key_id = data.aws_kms_key.logs.arn
}
