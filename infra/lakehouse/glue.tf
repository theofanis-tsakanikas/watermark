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

  target_database {
    catalog_id    = data.aws_caller_identity.current.account_id
    database_name = "${var.project}_bronze"
  }
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
    "table_type"                         = "ICEBERG"
    "format-version"                     = "2"
    "write.format.default"               = "parquet"
    "write.parquet.compression-codec"    = "zstd"
    # Merge-on-read. The erasure path (claim 6) issues row-level deletes against a subject, and
    # copy-on-write would rewrite whole files on every one of them. The cost is that reads merge
    # delete files until compaction runs, which is a job this layer schedules rather than a
    # service's cron.
    "write.delete.mode"                  = "merge-on-read"
    "write.update.mode"                  = "merge-on-read"
    "write.merge.mode"                   = "merge-on-read"
    # Snapshot retention is a policy in this repository, not a default. Anything a published
    # number was computed from is tagged, and the expiry job refuses to remove a tagged
    # snapshot — see maintenance.tf.
    "history.expire.max-snapshot-age-ms" = "2592000000"
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
