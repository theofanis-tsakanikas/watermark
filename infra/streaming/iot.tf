# IoT Core: one X.509 certificate per device, and a policy that lets a meter publish to exactly
# one topic — its own.
#
# The substitution variable `${iot:Connection.Thing.ThingName}` is the whole control. Without
# it, any meter's certificate can publish as any meter, and a single compromised device can
# forge readings for the entire fleet — which would be a tampering signature the anomaly
# classifier is specifically supposed to detect, arriving from the one source it trusts.
#
# There is no shared credential anywhere in this file. `docs/DECISIONS.md` and `CLAUDE.md` both
# say no long-lived access keys; a fleet-wide certificate is the same mistake wearing a
# different name.

resource "aws_iot_thing_type" "meter" {
  name = "${var.project}-meter"

  properties {
    description = "A residential or commercial smart meter reporting 15-minute interval energy"
  }
}

resource "aws_iot_thing_type" "charger" {
  name = "${var.project}-charger"

  properties {
    description = "A public EV charge point reporting session telemetry at 1 Hz"
  }
}

data "aws_iam_policy_document" "device" {
  statement {
    sid       = "ConnectOnlyAsItself"
    effect    = "Allow"
    actions   = ["iot:Connect"]
    resources = ["arn:aws:iot:${var.aws_region}:${data.aws_caller_identity.current.account_id}:client/$${iot:Connection.Thing.ThingName}"]
  }

  statement {
    sid     = "PublishOnlyToItsOwnTopic"
    effect  = "Allow"
    actions = ["iot:Publish"]
    resources = [
      "arn:aws:iot:${var.aws_region}:${data.aws_caller_identity.current.account_id}:topic/${var.project}/meter/$${iot:Connection.Thing.ThingName}/reading",
    ]
  }
}

resource "aws_iot_policy" "device" {
  name   = "${var.project}-device"
  policy = data.aws_iam_policy_document.device.json
}

# The rule that moves readings onto the stream.
#
# `topic(3)` is the thing name from the topic the device published to — which the policy above
# has already constrained to be the device's own. The partition key is therefore the meter id,
# established by the broker rather than taken from the payload: a producer that chose its own
# partition key could concentrate the whole fleet onto one shard, deliberately or by accident.
resource "aws_iot_topic_rule" "meter_readings" {
  name        = replace("${var.project}_meter_readings", "-", "_")
  description = "Forward device readings to the meter stream, keyed by the topic's thing name"
  enabled     = true
  # The envelope, shaped here rather than guessed downstream.
  #
  # `SELECT *` forwarded the device payload unchanged, and the Flink job deserialises a row of
  # three named fields — so every field came back null and the operator refused the record with
  # `ValueError: None is not a valid Source`. The row is an integration contract between this
  # rule and `streaming/job.py`, and a contract stated in one place only is one the other side
  # has to infer.
  #
  # `encode(*, 'base64')` because the payload is arbitrary device JSON and nesting it inside
  # another JSON document would mean escaping it; base64 travels through both intact, and
  # `normalise` in the core is what reads it. `topic(3)` is the thing name from
  # `<project>/meter/<thing>/reading` — the device's own, which the IoT policy already forces it
  # to publish under, so it cannot claim to be another meter.
  sql         = "SELECT encode(*, 'base64') AS raw, topic(3) AS partition, 'iot' AS source FROM '${var.project}/meter/+/reading'"
  sql_version = "2016-03-23"

  kinesis {
    role_arn      = aws_iam_role.iot_to_kinesis.arn
    stream_name   = aws_kinesis_stream.meter_readings.name
    partition_key = "$${topic(3)}"
  }

  # Where a record goes when the rule itself fails — a throttled stream, a permissions change.
  # Without it the reading is dropped and the only trace is a CloudWatch metric nobody has
  # alarmed on, which in a settlement pipeline is silent lost revenue.
  error_action {
    s3 {
      role_arn    = aws_iam_role.iot_to_kinesis.arn
      bucket_name = data.aws_s3_bucket.lakehouse.id
      key         = "quarantine/iot-rule-errors/$${timestamp()}"
    }
  }
}

data "aws_s3_bucket" "lakehouse" {
  bucket = "${var.project}-lakehouse-${data.aws_caller_identity.current.account_id}"
}

resource "aws_iam_role" "iot_to_kinesis" {
  name = "${var.project}-iot-to-kinesis"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "iot.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "iot_to_kinesis" {
  statement {
    effect    = "Allow"
    actions   = ["kinesis:PutRecord", "kinesis:PutRecords"]
    resources = [aws_kinesis_stream.meter_readings.arn]
  }

  statement {
    effect    = "Allow"
    actions   = ["kms:GenerateDataKey", "kms:Encrypt"]
    resources = [data.aws_kms_key.data.arn]
  }

  statement {
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${data.aws_s3_bucket.lakehouse.arn}/quarantine/iot-rule-errors/*"]
  }
}

resource "aws_iam_role_policy" "iot_to_kinesis" {
  name   = "publish-to-stream"
  role   = aws_iam_role.iot_to_kinesis.id
  policy = data.aws_iam_policy_document.iot_to_kinesis.json
}
