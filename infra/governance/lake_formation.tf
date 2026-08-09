# Lake Formation tag-based access control.
#
# Tags rather than per-table grants, because per-table grants are a list somebody maintains and
# a list somebody maintains is a list that is wrong. A tag says *what a column is*; the grant
# says *who may see that kind of thing*; and a new table inherits the policy by being tagged
# rather than by being remembered.
#
# `src/watermark/policy/` evaluates the same tags offline in Phase 4, the way Attestor evaluates
# Cedar — the deployed grants and the offline evaluator must read the same bytes, or the suite
# is checking a policy that is not the one in force.

resource "aws_lakeformation_resource_lf_tag" "sensitivity" {
  # Placeholder association; the tag itself is defined below. Kept separate so that adding a
  # tagged resource does not re-create the tag and invalidate every grant that references it.
  depends_on = [aws_lakeformation_lf_tag.sensitivity]

  database {
    name = "${var.project}_gold"
  }

  lf_tag {
    key   = aws_lakeformation_lf_tag.sensitivity.key
    value = "internal"
  }
}

resource "aws_lakeformation_lf_tag" "sensitivity" {
  key = "watermark:sensitivity"

  values = [
    # Nothing about a person. Substation load, thermal limits, network topology.
    "operational",
    # Business data that is not personal: balancing group totals, market positions.
    "internal",
    # Attributable to a household. Everything the erasure scope in claim 6 has to reach.
    "personal",
  ]
}

resource "aws_lakeformation_lf_tag" "purpose" {
  key = "watermark:purpose"

  # GDPR Art. 5(1)(b) in the access layer. A principal granted `settlement` may not read a
  # column collected for `network-operations`, even though both are in the same warehouse and
  # the same account — which is the difference between purpose limitation as a policy and
  # purpose limitation as a control.
  values = ["settlement", "network-operations", "fraud-investigation"]
}

# The grants. One per principal per tag combination, and each one is a decision written down.
resource "aws_lakeformation_permissions" "steward_personal" {
  for_each = toset(var.data_steward_principals)

  principal   = each.key
  permissions = ["SELECT"]

  lf_tag_policy {
    resource_type = "TABLE"

    expression {
      key    = aws_lakeformation_lf_tag.sensitivity.key
      values = ["personal"]
    }

    expression {
      key    = aws_lakeformation_lf_tag.purpose.key
      values = ["settlement"]
    }
  }
}
