# SageMaker Feature Store. Claim 3 lives here, and so does the reason it is not a tautology.
#
# `PutRecord` writes the online record and populates the offline store from the *same* call, so
# comparing the two compares a value with AWS's copy of it. ADR-0004's answer is that the
# offline side of the parity check is an independent recomputation from the raw lakehouse; what
# the Feature Store's own offline store is for is training, so that a model's inputs are the
# same objects the serving path produced.
#
# Three constraints from `docs/AWS-CONSTRAINTS.md` are visible in the schema below, and all
# three were verified rather than assumed:
#
#   * There are three value types and none of them is a decimal. Energy travels as a scaled
#     integer, which is what ADR-0004 chose instead of a comparison tolerance.
#   * The event time must be a String for an Iceberg-format feature group, in one of exactly
#     two ISO-8601 shapes — seconds, or nine fractional digits. This repository's canonical
#     instant renders three, so the adapter widens to nine. The core does not change: three is
#     what Flink carries.
#   * `write_time`, `api_invocation_time` and `is_deleted` are reserved. The first is what the
#     bitemporal parity query binds on.

locals {
  # The feature groups the contracts in `contracts/features/` will declare in phase 2. Named
  # here so the offline store's S3 prefixes and the erasure scope exist from the start: a
  # feature group added later without a prefix is a subject an erasure request cannot reach.
  feature_groups = {
    substation_load = {
      record_identifier = "substation_id"
      description       = "Short-horizon load features per substation, for the curtailment forecast"
      personal_data     = false
      # The value columns this group serves, one per feature contract that reads it. Declared
      # per group rather than once for all of them: the first version gave every group a single
      # `energy_wh`, which is the meter's column — so the two substation features had nowhere to
      # be written and `parity_live.py` could not have compared them if anybody had asked it to.
      values = ["load_w", "headroom_w"]
    }
    meter_consumption = {
      record_identifier = "meter_id"
      description       = "Consumption features per meter, for the anomaly classifier"
      personal_data     = true
      values            = ["energy_wh"]
    }
  }
}

resource "aws_sagemaker_feature_group" "features" {
  for_each = local.feature_groups

  feature_group_name             = "${var.project}-${replace(each.key, "_", "-")}"
  description                    = each.value.description
  record_identifier_feature_name = each.value.record_identifier
  event_time_feature_name        = "event_time"
  role_arn                       = aws_iam_role.feature_store.arn

  feature_definition {
    feature_name = each.value.record_identifier
    feature_type = "String"
  }

  feature_definition {
    feature_name = "event_time"
    # String, not Fractional. An Iceberg-format offline store accepts only String, and the
    # accepted shapes are `yyyy-MM-dd'T'HH:mm:ssZ` and `yyyy-MM-dd'T'HH:mm:ss.SSSSSSSSSZ` —
    # neither of which is the three-decimal form this repository renders. The adapter widens.
    feature_type = "String"
  }

  # Integral, every one of them. There is no decimal type, and a double compared against
  # `decimal(18,3)` in Iceberg differs in the last bits by construction — which is why ADR-0004
  # forbids a tolerance and requires a declared scale instead.
  dynamic "feature_definition" {
    for_each = each.value.values

    content {
      feature_name = feature_definition.value
      feature_type = "Integral"
    }
  }

  offline_store_config {
    disable_glue_table_creation = false

    s3_storage_config {
      s3_uri     = "s3://${data.aws_s3_bucket.lakehouse.id}/feature-store/${each.key}"
      kms_key_id = data.aws_kms_key.data.arn
    }

    # Iceberg, so the offline store is a table the erasure path can issue row-level deletes
    # against. A Glue-format offline store is append-only files, and claim 6 would then have
    # one leg it could only satisfy by rewriting the prefix.
    table_format = "Iceberg"
  }

  dynamic "online_store_config" {
    for_each = var.online_store_enabled ? [1] : []

    content {
      enable_online_store = true

      security_config {
        kms_key_id = data.aws_kms_key.data.arn
      }
    }
  }
}

resource "aws_iam_role" "feature_store" {
  name = "${var.project}-feature-store"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "feature_store" {
  statement {
    effect = "Allow"
    # `GetBucketAcl` is not decoration. Feature Store validates the offline-store S3 URI at
    # CreateFeatureGroup time by calling it, so without this the create fails with "Invalid
    # S3Uri provided" — a message about the URI, caused by a missing permission on the bucket.
    actions = [
      "s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket",
      "s3:GetBucketLocation", "s3:GetBucketAcl",
    ]
    resources = [
      data.aws_s3_bucket.lakehouse.arn,
      "${data.aws_s3_bucket.lakehouse.arn}/feature-store/*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [data.aws_kms_key.data.arn]
  }

  statement {
    effect  = "Allow"
    actions = ["glue:CreateTable", "glue:GetTable", "glue:UpdateTable", "glue:GetDatabase"]
    resources = [
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/sagemaker_featurestore",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/sagemaker_featurestore/*",
    ]
  }
}

resource "aws_iam_role_policy" "feature_store" {
  name   = "materialise-features"
  role   = aws_iam_role.feature_store.id
  policy = data.aws_iam_policy_document.feature_store.json
}
