# Glue Data Quality as a gate on the offline side.
#
# **This lives in `governance` and not in `lakehouse`, and the reason is an ordering.** A data
# quality ruleset attaches to a table, and after ADR-0008 the tables an Iceberg engine writes are
# created by that engine rather than by Terraform — so at the moment the lakehouse layer applies,
# `silver.meter_interval` does not exist yet and a ruleset naming it fails with
# `EntityNotFoundException`. `deploy.yml` runs the merge job once, with nothing landed, between
# the lakehouse layer and this one for exactly this reason: the job's `CREATE TABLE IF NOT EXISTS`
# is what brings the table into being, and this layer is the last to apply.
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
#
#                   **This rule had the same disease as the one removed above, and it took the
#                   first evaluation to find it.** It was written
#                   `(ColumnValues "revision" = 0) OR (ColumnLength "supersedes" > 0)`, and
#                   `ColumnLength` is a string function against a `bigint` column:
#
#                       Expected type of column supersedes to be StringType, but found LongType
#
#                   The type is only half of it. `OR` in DQDL composes two *rule outcomes*, not
#                   two row predicates — so even with the types fixed, a table holding both
#                   revision-0 and revision-1 rows fails the left side and fails the right side
#                   and the composition can never pass. What the doctrine actually says is a
#                   statement about a subset of rows, and `where` is the construct for that —
#                   placed *after* the expression, which is the second thing the account had to
#                   teach: `Completeness "supersedes" where "revision > 0" = 1.0` is refused with
#                   "DataQuality rules cannot be parsed", and the whole ruleset with it.
#   lineage_id    — claim 2's identity. It was `Completeness = 1.0` against a column the
#                   streaming adapter never wrote, so the rule would have failed every run had
#                   the table it names ever existed to be scanned.
resource "aws_glue_data_quality_ruleset" "meter_interval" {
  name        = "${var.project}-meter-interval"
  description = "Invariants the stream core guarantees. A violation is a bug, never a weather event."

  target_table {
    database_name = "${var.project}_silver"
    table_name    = "meter_interval"
  }

  ruleset = <<-DQDL
    Rules = [
      IsPrimaryKey "meter_id" "interval_start" "revision",
      ColumnValues "energy_wh" >= 0,
      Completeness "supersedes" = 1.0 where "revision > 0",
      Completeness "meter_id" = 1.0,
      Completeness "lineage_id" = 1.0,
      Completeness "closed_at" = 1.0
    ]
  DQDL
}

# **`gold.settlement_hour` has no ruleset here, and that is a gap rather than a decision.**
#
# It had one. It named a table dbt builds, and dbt cannot build it until the silver table holds
# rows — so the table does not exist at any point during a deploy, and a ruleset naming it
# cannot be applied at all. Carrying it anyway would have meant a layer that fails on every
# apply, and carrying it against the Terraform stub meant what the stub meant: nothing.
#
# It returns when the gold layer is built inside the capture rather than by hand. Until then the
# invariants it asserted — one row per meter and hour, between one and four intervals in an
# hour, a lineage id on every row — are checked only by the dbt tests in `pipelines/dbt/tests/`,
# which is a weaker place because nothing runs them yet either.

# **Lake Formation for the erasure role, and it is the half IAM does not cover.**
#
# The first live erasure returned, from two of its five legs:
#
#     Access Denied when accessing database watermark_silver, table meter_interval
#     Access Denied when accessing database watermark_gold, table training_snapshot
#
# The role's IAM policy allows `athena:StartQueryExecution` and the S3 and KMS it needs. It ran
# the queries. Lake Formation refused the tables, because this account grants
# `CreateTableDefaultPermissions` to nobody and a table's creator is the only principal with
# rights to it until somebody says otherwise.
#
# `DELETE` and `SELECT`, and `DESCRIBE` so a failure names the table rather than pretending it
# does not exist. Not `DROP`: the erasure removes a subject's rows, and a role that could remove
# the table would turn the worst kind of bug into the worst kind of outage.
#
# **And `ALTER`, which the first run without it explained:**
#
#     Insufficient Lake Formation permission(s): Required Alter on meter_interval
#
# An Iceberg `DELETE` does not delete in place. It writes new data and delete files and then
# commits a new metadata pointer, and moving that pointer is an alteration of the table rather
# than a modification of its rows. So a role that may delete rows and may not alter the table
# can do neither — the permission model draws its line where Iceberg does not.
#
# `ALTER` is not `DROP` and the gap between them is the point: this role can rewrite which files
# the table consists of, and cannot make the table stop existing.
#
# This is the third principal in this estate to need the same lesson — the merge job, the
# workflow that verifies it, and now the orchestration that erases. Worth naming: an IAM policy
# that reads correctly is not evidence of access when Lake Formation is in force.
resource "aws_lakeformation_permissions" "erasure_silver" {
  principal   = aws_iam_role.erasure.arn
  permissions = ["SELECT", "DELETE", "DESCRIBE", "ALTER"]

  table {
    database_name = "${var.project}_silver"
    wildcard      = true
  }
}

resource "aws_lakeformation_permissions" "erasure_gold" {
  principal   = aws_iam_role.erasure.arn
  permissions = ["SELECT", "DELETE", "DESCRIBE", "ALTER"]

  table {
    database_name = "${var.project}_gold"
    wildcard      = true
  }
}
