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
      "arn:aws:iot:${var.aws_region}:${data.aws_caller_identity.current.account_id}:topic/${var.project}/meter/+/$${iot:Connection.Thing.ThingName}/reading",
      # And its own backfill topic. Still its own: the head-end corrects readings *for* a meter,
      # and a publisher that could write to another meter's backfill could restate somebody
      # else's consumption.
      "arn:aws:iot:${var.aws_region}:${data.aws_caller_identity.current.account_id}:topic/${var.project}/meter/+/$${iot:Connection.Thing.ThingName}/backfill",
    ]
  }
}

resource "aws_iot_policy" "device" {
  name   = "${var.project}-device"
  policy = data.aws_iam_policy_document.device.json
}

# The rule that moves readings onto the stream.
#
# **The topic carries the substation, and that is the whole of a bug worth reading about.**
#
# The topic used to be `<project>/meter/<thing>/reading`, so `topic(3)` was the meter id, and the
# rule put the meter id in the field the core calls `partition`. The core declares its partitions
# from `WATERMARK_PARTITIONS`, which is the substation list out of `data/cast.py`. Two
# vocabularies, one field name.
#
# What that produced in a live run, visible on every published row:
#
#     "holding_back": "M00038",
#     "idle_partitions": ["SUB-01", "SUB-02", "SUB-03", "SUB-04"]
#
# Every declared substation lagged infinitely because no record ever named one, so all four were
# excluded as idle for ever, and the watermark was pinned by whichever *meter* happened to be
# slowest. Every total was published `advancing_with_idle` and carried a hole that did not exist.
# Worse, claim 1's sharpest case could not fire at all: SUB-03 going quiet for forty minutes is
# invisible when SUB-03 never speaks in the first place.
#
# So the topic is `<project>/meter/<substation>/<thing>/reading`. `topic(3)` is the substation —
# the comms domain that actually goes silent, which is what a watermark partition has to be — and
# `topic(4)` is the thing name, still pinned to the device's own by the policy above, so a device
# still cannot publish as another meter. The Kinesis partition key is the meter, because shard
# distribution wants the high-cardinality key and the watermark wants the physical one; they were
# the same field until now only because both were wrong.
#
# The substation segment is *not* pinned by the policy, and that is a real weakening stated
# rather than hidden: a device could claim to sit under a substation it does not. In a deployment
# the segment is provisioned with the certificate; here the honest note is that meter identity is
# what the policy protects and topology is not.
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
  # `'stream'`, not `'iot'`: the value is a `watermark.core.records.Source`, whose members are
  # `stream` and `batch` — the distinction the core cares about is live arrival versus the
  # three-day-late head-end file, not which AWS service carried it. The first live run raised
  # `ValueError: 'iot' is not a valid Source` inside the core, which was right to refuse it.
  #
  # `encode(*, 'base64')` because the payload is arbitrary device JSON and nesting it inside
  # another JSON document would mean escaping it; base64 travels through both intact, and
  # `normalise` in the core is what reads it. `topic(3)` is the substation from
  # `<project>/meter/<substation>/<thing>/reading` — the partition the core declares — and
  # `topic(4)` is the thing name the IoT policy forces the device to publish under, so it cannot
  # claim to be another meter.
  sql         = "SELECT encode(*, 'base64') AS raw, topic(3) AS partition, 'stream' AS source FROM '${var.project}/meter/+/+/reading'"
  sql_version = "2016-03-23"

  kinesis {
    role_arn      = aws_iam_role.iot_to_kinesis.arn
    stream_name   = aws_kinesis_stream.meter_readings.name
    partition_key = "$${topic(4)}"
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

# The second arrival path, and the reason there are two.
#
# The head-end's three-day-late file is not a device reading that happened to be slow — it is a
# **correction**, and `watermark.core.records.Source` distinguishes the two because everything
# downstream depends on it: a late `stream` record is refused as past its window, while a
# `batch` record restates a total that was already published and keeps the prior value, the
# reason and the delta (doctrine 4).
#
# One topic and one rule cannot express that. The first live run published all 4,312 deliveries
# — 4,024 stream and **288 batch** — to `/reading`, so every correction arrived claiming to be
# a live reading, was refused as too late, and not one restatement was ever produced. The edge
# case that carries doctrine 4 could not reach the system at all.
resource "aws_iot_topic_rule" "meter_backfill" {
  name        = replace("${var.project}_meter_backfill", "-", "_")
  description = "Forward the head-end's late file to the meter stream, labelled as the batch it is"
  enabled     = true
  # The envelope, shaped here rather than guessed downstream.
  #
  # `SELECT *` forwarded the device payload unchanged, and the Flink job deserialises a row of
  # three named fields — so every field came back null and the operator refused the record with
  # `ValueError: None is not a valid Source`. The row is an integration contract between this
  # rule and `streaming/job.py`, and a contract stated in one place only is one the other side
  # has to infer.
  #
  # `'stream'`, not `'iot'`: the value is a `watermark.core.records.Source`, whose members are
  # `stream` and `batch` — the distinction the core cares about is live arrival versus the
  # three-day-late head-end file, not which AWS service carried it. The first live run raised
  # `ValueError: 'iot' is not a valid Source` inside the core, which was right to refuse it.
  #
  # `encode(*, 'base64')` because the payload is arbitrary device JSON and nesting it inside
  # another JSON document would mean escaping it; base64 travels through both intact, and
  # `normalise` in the core is what reads it. `topic(3)` is the substation from
  # `<project>/meter/<substation>/<thing>/reading` — the partition the core declares — and
  # `topic(4)` is the thing name the IoT policy forces the device to publish under, so it cannot
  # claim to be another meter.
  sql         = "SELECT encode(*, 'base64') AS raw, topic(3) AS partition, 'batch' AS source FROM '${var.project}/meter/+/+/backfill'"
  sql_version = "2016-03-23"

  kinesis {
    role_arn      = aws_iam_role.iot_to_kinesis.arn
    stream_name   = aws_kinesis_stream.meter_readings.name
    partition_key = "$${topic(4)}"
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
