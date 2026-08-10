# The ceiling, and it lives here rather than in `foundation`.
#
# A budget created by the same apply that creates the spending is a guard that does not exist
# while the spending starts, and a foundation apply that fails halfway leaves resources standing
# with nothing watching them. Bootstrap runs before anything can be deployed at all, which is
# the only moment a ceiling is worth putting in place.
#
# **The action is the control. The notifications are not.** At the threshold the deploy role's
# permissions are detached, so nothing more can be created — CI keeps working, keeps assuming
# the role, and keeps failing on access denials that name the action they wanted. That is a
# legible failure. An email at 60% is somebody reading it on Monday.

resource "aws_budgets_budget" "estate" {
  name         = "${var.project}-estate"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)

  # USD, and not by preference: the API refuses every other unit for this account.
  # `EUR` passes `terraform validate` and fails the apply. See the variable's description.
  limit_unit = "USD"
  time_unit  = "MONTHLY"

  # Only what this project tagged. The account holds other work, and a ceiling that counts
  # somebody else's spend disables this project's deploy role for a bill it did not run up.
  #
  # `format`, and not a template string. AWS wants `user:<tag key>$<tag value>`, and the `$`
  # separator collides with Terraform's interpolation: written as
  # `"user:watermark:project$${var.project}"` the `$${` is read as the escape for a literal
  # `${`, so the filter becomes the eleven characters `${var.project}` and matches no resource
  # that has ever existed. The budget then counts zero for ever and the ceiling never fires —
  # a cost guard that is present, green, and incapable of doing anything.
  #
  # This layer had that bug, inherited from `foundation`, until an apply against the real
  # account showed the stored value back.
  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:watermark:project$%s", var.project)]
  }

  # Directly to an address, with no SNS topic in between. An SNS email subscription has to be
  # confirmed by clicking a link, and until it is the topic has no confirmed endpoint and the
  # alarm is silent — a manual step that fails closed on nothing and open on everything.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 60
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}

# What the action attaches. An explicit deny beats detaching the allow policies: a deny cannot
# be out-voted by a permission somebody adds later, and reattaching the allows is not enough to
# undo it — which is the point. Getting spending going again is meant to take a decision.
resource "aws_iam_policy" "deny_everything" {
  name        = "${var.project}-budget-ceiling-reached"
  description = "Attached to the deploy role by the budget action. Detach it deliberately, after deciding why the spend happened."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "TheBudgetCeilingWasReached"
      Effect   = "Deny"
      Action   = "*"
      Resource = "*"
    }]
  })
}

data "aws_iam_policy_document" "budget_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    # Without this a service principal in any account could assume the role. The pair is what
    # AWS documents as the confused-deputy guard for budget actions.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:budgets::${data.aws_caller_identity.current.account_id}:budget/*"]
    }
  }
}

resource "aws_iam_role" "budget_action" {
  name               = "${var.project}-budget-action"
  description        = "Assumed by AWS Budgets to attach the ceiling policy to the deploy role."
  assume_role_policy = data.aws_iam_policy_document.budget_assume.json
}

# It may attach one policy to one role, and read that one policy. Nothing is on `*`.
#
# This identity exists to take a single action, and every widening of it is a widening of what
# an attacker who reaches the budget service can do. The obvious first draft grants `budgets:*`
# as well; the execution role never calls Budgets — Budgets calls *it* — so that grant is
# permission for a caller that does not exist.
resource "aws_iam_role_policy" "budget_action" {
  name = "attach-the-ceiling"
  role = aws_iam_role.budget_action.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AttachTheCeilingToTheDeployRole"
        Effect   = "Allow"
        Action   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListAttachedRolePolicies"]
        Resource = aws_iam_role.deploy.arn
      },
      {
        Sid      = "ReadTheCeilingItAttaches"
        Effect   = "Allow"
        Action   = ["iam:GetPolicy"]
        Resource = aws_iam_policy.deny_everything.arn
      },
    ]
  })
}

resource "aws_budgets_budget_action" "ceiling" {
  budget_name        = aws_budgets_budget.estate.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.deny_everything.arn
      roles      = [aws_iam_role.deploy.name]
    }
  }

  subscriber {
    address           = var.budget_alert_email
    subscription_type = "EMAIL"
  }
}

# Activating the tag, which is the step that makes the filter above mean anything.
#
# A cost allocation tag key is inert until it is switched on in Billing: until then the filter
# matches nothing, the budget reports zero, and the ceiling is a resource that exists and cannot
# fire. The console is the usual place this is done, which is why it is usually forgotten and
# never noticed — a budget at zero looks exactly like a project that is not spending.
#
# AWS only lists a key here once it has seen it on a billed resource, which can take up to 24
# hours after the first tagged thing is created. So a bootstrap applied into a fresh account may
# fail on this one resource, and the fix is to apply again the next day rather than to reach for
# the console.
resource "aws_ce_cost_allocation_tag" "project" {
  tag_key = "watermark:project"
  status  = "Active"
}
