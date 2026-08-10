# Glue Data Quality as a gate on the offline side.
#
# The rules are deliberately the ones a *correct* pipeline cannot violate, so that a failure
# means the pipeline is wrong rather than the data being unusual. A rule about consumption
# levels would fire on a cold week; these fire only on defects.

# The reasoning lives here, not inside the DQDL.
#
# DQDL is a small language with no comment syntax, and Glue rejects the whole ruleset with
# "DataQuality rules cannot be parsed" when it finds one. The rules below used to carry their
# justifications inline; those are now here, where they can be as long as they need to be.
#
# `ColumnValues "closed_at" >= "interval_start"` was also removed. It read as claim 1 checkable
# in the warehouse — a row published with a watermark earlier than its own interval end is a
# decision taken on a window that had not closed — but DQDL compares a column against a
# *literal*, not against another column, so the rule was never going to parse. Claim 1 is
# proved in `evals/watermark/`, which is where it belongs; the warehouse check needs a
# different mechanism and is not being faked here.
#
# What remains:
#   IsPrimaryKey  — deduplication happens before publication, so exactly one row may exist per
#                   meter, interval and revision. Two is a customer billed twice.
#   energy_wh >= 0 — these meters do not export. A negative total is a fault or a tamper
#                   signature, and either way it is evidence rather than a measurement.
#   revision/supersedes — doctrine 4: a revision states what it replaced. A restatement with an
#                   empty `supersedes` has erased the prior value instead of superseding it.
resource "aws_glue_data_quality_ruleset" "meter_interval" {
  name        = "${var.project}-meter-interval"
  description = "Invariants the stream core guarantees. A violation is a bug, never a weather event."

  target_table {
    database_name = aws_glue_catalog_database.silver.name
    table_name    = aws_glue_catalog_table.meter_interval.name
  }

  ruleset = <<-DQDL
    Rules = [
      IsPrimaryKey "meter_id" "interval_start" "revision",
      ColumnValues "energy_wh" >= 0,
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
      ColumnValues "intervals" <= 4,
      ColumnValues "intervals" >= 1,
      ColumnValues "energy_wh" >= 0,
      IsPrimaryKey "meter_id" "hour_start",
      Completeness "lineage_id" = 1.0
    ]
  DQDL
}
