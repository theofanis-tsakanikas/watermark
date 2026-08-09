# Glue Data Quality as a gate on the offline side.
#
# The rules are deliberately the ones a *correct* pipeline cannot violate, so that a failure
# means the pipeline is wrong rather than the data being unusual. A rule about consumption
# levels would fire on a cold week; these fire only on defects.

resource "aws_glue_data_quality_ruleset" "meter_interval" {
  name        = "${var.project}-meter-interval"
  description = "Invariants the stream core guarantees. A violation is a bug, never a weather event."

  target_table {
    database_name = aws_glue_catalog_database.silver.name
    table_name    = aws_glue_catalog_table.meter_interval.name
  }

  ruleset = <<-DQDL
    Rules = [
      # Claim 1, checkable in the warehouse after the fact and without re-running anything.
      # A row published with a watermark earlier than its own interval end is a decision taken
      # on a window that had not closed.
      ColumnValues "closed_at" >= "interval_start",

      # Deduplication happens before publication, so exactly one row may exist per meter,
      # interval and revision. Two is a duplicate that survived, which is a customer billed
      # twice for the same electricity.
      IsPrimaryKey "meter_id" "interval_start" "revision",

      # These meters do not export. A negative total is a fault or a tamper signature, and
      # either way it is evidence rather than a measurement.
      ColumnValues "energy_wh" >= 0,

      # Doctrine 4: a revision states what it replaced. A restatement with nothing in
      # `supersedes` has erased the prior value instead of superseding it.
      (ColumnValues "revision" = 0) OR (ColumnLength "supersedes" > 0),

      Completeness "meter_id" = 1.0,
      Completeness "lineage_id" = 1.0,
      Completeness "closed_at" = 1.0
    ]
  DQDL
}

resource "aws_glue_data_quality_ruleset" "settlement_hour" {
  name        = "${var.project}-settlement-hour"
  description = "What an invoice line may not contain"

  target_table {
    database_name = aws_glue_catalog_database.gold.name
    table_name    = aws_glue_catalog_table.settlement_hour.name
  }

  ruleset = <<-DQDL
    Rules = [
      # Four fifteen-minute intervals make an hour. More than four is double-counting; the
      # `is_complete` flag carries fewer than four honestly, so the rule bounds the top only.
      ColumnValues "intervals" <= 4,
      ColumnValues "intervals" >= 1,
      ColumnValues "energy_wh" >= 0,
      IsPrimaryKey "meter_id" "hour_start",
      Completeness "lineage_id" = 1.0
    ]
  DQDL
}
