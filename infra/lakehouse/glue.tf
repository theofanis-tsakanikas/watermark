# Iceberg on S3 with the Glue Data Catalog. ADR-0002 chose this over S3 Tables on one fact:
# S3 Tables' snapshot management does not honour Iceberg branch or tag retention and switches
# itself off if one is configured — and a tag is the mechanism that keeps alive the snapshot a
# published settlement total was computed from.
#
# Owning the three maintenance routines is the price that decision accepted, and `maintenance.tf`
# is where it is paid.

resource "aws_glue_catalog_database" "bronze" {
  name        = "${var.project}_bronze"
  description = "Raw normalised records as they arrived, before any window closed"

  # No `target_database`. It used to point at this database's own name, which makes it a
  # *resource link to itself* — a Lake Formation construct for sharing a database across
  # accounts, not a way to describe one. Glue rejects it outright: "Description and resource
  # link cannot exist together in a database!"
}

resource "aws_glue_catalog_database" "silver" {
  name        = "${var.project}_silver"
  description = "Closed windows and their restatements — the output of the stream core"
}

resource "aws_glue_catalog_database" "gold" {
  name        = "${var.project}_gold"
  description = "Settled hours, balancing groups and decision records — what anything downstream reads"
}

locals {
  warehouse = "s3://${data.aws_s3_bucket.lakehouse.id}/warehouse"

  # Iceberg table properties applied to every table. Written once because a table that missed
  # one of them is a table whose maintenance behaves differently from every other, and nothing
  # about it looks different.
  iceberg_properties = {
    "table_type"                      = "ICEBERG"
    "format-version"                  = "2"
    "write.format.default"            = "parquet"
    "write.parquet.compression-codec" = "zstd"
    # Merge-on-read. The erasure path (claim 6) issues row-level deletes against a subject, and
    # copy-on-write would rewrite whole files on every one of them. The cost is that reads merge
    # delete files until compaction runs, which is a job this layer schedules rather than a
    # service's cron.
    "write.delete.mode" = "merge-on-read"
    "write.update.mode" = "merge-on-read"
    "write.merge.mode"  = "merge-on-read"
    # Snapshot retention is a policy in this repository, not a default. Anything a published
    # number was computed from is tagged, and the expiry job refuses to remove a tagged
    # snapshot — see maintenance.tf.
    "history.expire.max-snapshot-age-ms"   = "2592000000"
    "history.expire.min-snapshots-to-keep" = "10"
  }
}

resource "aws_glue_catalog_table" "meter_interval" {
  name          = "meter_interval"
  database_name = aws_glue_catalog_database.silver.name
  table_type    = "EXTERNAL_TABLE"
  parameters    = local.iceberg_properties

  storage_descriptor {
    location = "${local.warehouse}/silver/meter_interval"

    columns {
      name = "meter_id"
      type = "string"
    }
    columns {
      name = "interval_start"
      type = "timestamp"
    }
    columns {
      name    = "energy_wh"
      type    = "bigint"
      comment = "Integer watt-hours. Not a decimal and not a double: ADR-0004 forbids a tolerance in the parity comparison, and a scaled integer is what replaces it."
    }
    columns {
      name = "readings"
      type = "int"
    }
    columns {
      name = "duplicates_suppressed"
      type = "int"
    }
    columns {
      name = "corrections_absorbed"
      type = "int"
    }
    columns {
      name    = "closed_at"
      type    = "timestamp"
      comment = "The watermark that permitted publication. A row whose closed_at precedes its own interval end could not have come from the core — which makes claim 1 checkable in SQL, after the fact, without re-running anything."
    }
    columns {
      name = "watermark_status"
      type = "string"
    }
    columns {
      name    = "idle_partitions"
      type    = "array<string>"
      comment = "Substations excluded from the watermark when this row was published. The hole travels to the invoice."
    }
    columns {
      name = "first_seen_at"
      type = "timestamp"
    }
    columns {
      name = "revision"
      type = "int"
    }
    columns {
      name = "supersedes"
      type = "bigint"
    }
    columns {
      name = "restatement_cause"
      type = "string"
    }
    columns {
      name = "lineage_id"
      type = "string"
    }
  }

  # Partitioned by day of the interval, not by ingestion date. Settlement asks "what happened on
  # the 14th", and a table partitioned by when the record arrived answers that by scanning
  # everything — including the three-day-late file, which is precisely the row it needs.
  partition_keys {
    name = "interval_day"
    type = "date"
  }
}

resource "aws_glue_catalog_table" "settlement_hour" {
  name          = "settlement_hour"
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"
  parameters    = local.iceberg_properties

  storage_descriptor {
    location = "${local.warehouse}/gold/settlement_hour"

    columns {
      name = "meter_id"
      type = "string"
    }
    columns {
      name = "hour_start"
      type = "timestamp"
    }
    columns {
      name = "energy_wh"
      type = "bigint"
    }
    columns {
      name = "intervals"
      type = "int"
    }
    columns {
      name = "revision"
      type = "int"
    }
    columns {
      name = "is_complete"
      type = "boolean"
    }
    columns {
      name = "computed_with_idle_partition"
      type = "boolean"
    }
    columns {
      name = "lineage_id"
      type = "string"
    }
  }

  partition_keys {
    name = "hour_day"
    type = "date"
  }
}

resource "aws_glue_catalog_table" "quarantine" {
  name          = "quarantine"
  database_name = aws_glue_catalog_database.bronze.name
  table_type    = "EXTERNAL_TABLE"
  parameters    = local.iceberg_properties

  storage_descriptor {
    location = "${local.warehouse}/bronze/quarantine"

    columns {
      name    = "reason"
      type    = "string"
      comment = "From the closed vocabulary in core/quarantine.py. Closed so the queue can be counted: a hundred variations of 'bad timestamp' cannot be aggregated, and the first thing anybody asks of a quarantine queue is how many, of what."
    }
    columns {
      name = "disposition"
      type = "string"
    }
    columns {
      name = "detail"
      type = "string"
    }
    columns {
      name = "payload"
      type = "string"
    }
    columns {
      name = "quarantined_at"
      type = "timestamp"
    }
  }

  partition_keys {
    name = "quarantined_day"
    type = "date"
  }
}


# ── Tables the policy tags and something outside this layer populates ────────
#
# They exist here because a resource the Lake Formation policy governs and no layer creates is
# a grant that selects nothing — and a grant that selects nothing looks exactly like a grant
# that works. `scripts/check_policy_access.py` compares the two lists.
#
# The SCD-2 reference tables are landed by CDC from the operational database. That pipeline is
# **not in this repository**: it is a DMS task against a database that does not exist, and
# modelling it would be modelling the fiction rather than the platform. What is here is the
# shape the platform reads, so that a query written against it is checkable and the erasure
# orchestration has something real to issue a DELETE against.

resource "aws_glue_catalog_table" "substation_telemetry" {
  name          = "substation_telemetry"
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"
  parameters    = local.iceberg_properties

  storage_descriptor {
    location = "${local.warehouse}/gold/substation_telemetry"

    columns {
      name = "substation_id"
      type = "string"
    }
    columns {
      name = "event_time"
      type = "timestamp"
    }
    columns {
      name = "ingest_time"
      type = "timestamp"
    }
    columns {
      name    = "load_w"
      type    = "bigint"
      comment = "Measured load in watts. Integer: the curtailment fallback compares it against a limit, and a float comparison inside a safety path is a comparison two engines can disagree about."
    }
    columns {
      name = "limit_w"
      type = "bigint"
    }
  }

  partition_keys {
    name = "event_day"
    type = "date"
  }
}

resource "aws_glue_catalog_table" "inspection_outcome" {
  name          = "inspection_outcome"
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"
  parameters    = local.iceberg_properties

  storage_descriptor {
    location = "${local.warehouse}/gold/inspection_outcome"

    columns {
      name = "meter_id"
      type = "string"
    }
    columns {
      name = "inspected_at"
      type = "timestamp"
    }
    columns {
      name    = "reviewer"
      type    = "string"
      comment = "The named human. AI Act Art. 14 — oversight by an unnamed principal is not oversight, and claim 7 rests on the name being in the record."
    }
    columns {
      name = "verdict"
      type = "string"
    }
    columns {
      name    = "reason"
      type    = "string"
      comment = "Required on a rejection. A rejection is a training signal, and one with no reason teaches the next model that the inspector was arbitrary — see docs/BIAS-FINDING.md."
    }
  }

  partition_keys {
    name = "inspected_day"
    type = "date"
  }
}

resource "aws_glue_catalog_table" "meter_assignment_scd2" {
  name          = "meter_assignment_scd2"
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"
  parameters    = local.iceberg_properties

  storage_descriptor {
    location = "${local.warehouse}/gold/meter_assignment_scd2"

    columns {
      name = "meter_id"
      type = "string"
    }
    columns {
      name = "customer_id"
      type = "string"
    }
    columns {
      name    = "valid_from"
      type    = "timestamp"
      comment = "Half-open interval, matching src/watermark/core/pit.py. Closed on both sides makes two versions valid at a boundary, and which one a query returns becomes a property of its ORDER BY."
    }
    columns {
      name = "valid_to"
      type = "timestamp"
    }
  }
}

resource "aws_glue_catalog_table" "customer_scd2" {
  name          = "customer_scd2"
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"
  parameters    = local.iceberg_properties

  storage_descriptor {
    location = "${local.warehouse}/gold/customer_scd2"

    columns {
      name = "customer_id"
      type = "string"
    }
    columns {
      name = "balancing_group"
      type = "string"
    }
    columns {
      name = "postcode_area"
      type = "string"
    }
    columns {
      name = "valid_from"
      type = "timestamp"
    }
    columns {
      name = "valid_to"
      type = "timestamp"
    }
  }
}

resource "aws_glue_catalog_table" "training_snapshot" {
  name          = "training_snapshot"
  database_name = aws_glue_catalog_database.gold.name
  table_type    = "EXTERNAL_TABLE"
  parameters    = local.iceberg_properties

  storage_descriptor {
    location = "${local.warehouse}/gold/training_snapshot"

    columns {
      name    = "customer_id"
      type    = "string"
      comment = "Present so that an erasure can issue a row-level DELETE against the training sets. A snapshot keyed only by meter would leave the subject reachable through a reassignment."
    }
    columns {
      name = "meter_id"
      type = "string"
    }
    columns {
      name = "snapshot_id"
      type = "string"
    }
    columns {
      name = "features"
      type = "string"
    }
    columns {
      name = "label"
      type = "int"
    }
  }
}

# The maintenance job scripts, uploaded so that the Glue jobs above have something to run.
#
# Glue does not validate that a script exists at apply time. Without these objects the apply
# succeeds, every maintenance run then fails, and the failure surfaces as the erasure Step
# Function timing out on a compaction that was never going to start.
resource "aws_s3_object" "maintenance_job" {
  for_each = toset(["compaction", "expire_snapshots", "delete_orphan_files"])

  bucket = data.aws_s3_bucket.lakehouse.id
  key    = "jobs/${each.key}.py"
  source = "${path.module}/../../pipelines/jobs/${each.key}.py"
  # `source_hash`, not `etag`: with SSE-KMS the ETag is not the content MD5 and the provider
  # refuses the pair. Same defect as `infra/streaming/flink.tf` had, found by reading rather
  # than by a second failed apply. `filemd5` is safe here — these files are committed, not
  # build output, so validate can always read them.
  source_hash = filemd5("${path.module}/../../pipelines/jobs/${each.key}.py")
  kms_key_id  = data.aws_kms_key.data.arn
}
