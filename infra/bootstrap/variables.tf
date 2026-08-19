variable "project" {
  description = "Prefix for every resource name and the value of the watermark:project tag."
  type        = string
  default     = "watermark"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.project))
    error_message = "The project prefix is lower-case letters, digits and hyphens, 3 to 21 characters — it becomes part of an S3 bucket name."
  }
}

variable "aws_region" {
  description = "Region for the state backend and the OIDC role."
  type        = string
  default     = "eu-central-1"
}

variable "github_owner" {
  description = "GitHub account that owns the repository CI runs from."
  type        = string
}

variable "budget_alert_email" {
  description = <<-EOT
    Where the foundation layer's budget alarm goes.

    An account fact rather than a repository one, so it is set here, once, by the person who
    applies this layer, and published to SSM for the deploy to read. The alternative is a
    repository variable holding somebody's address, which puts personal data in a settings page
    that survives every later decision about who can read this repository.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.budget_alert_email))
    error_message = "budget_alert_email must be an address. The budget alarm is the last line of the cost controls; a typo here is silence at exactly the wrong moment."
  }
}

variable "github_repo" {
  description = "Repository name. Together with the owner and an environment this is the whole of what the deploy role trusts."
  type        = string
  default     = "watermark"
}

# The ids behind those two names.
#
# A GitHub account name can be released and re-registered by somebody else; a numeric id cannot.
# GitHub's subject claim can carry either form, and which one an account sends is not decided by
# this file — so the trust accepts both, and both are equally specific. Read them once with:
#
#   gh api users/<owner> --jq .id
#   gh api repos/<owner>/<repo> --jq .id

variable "github_owner_id" {
  description = "Immutable numeric id of the owner. The name form of the subject is the one that can be taken over; this one cannot."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "github_owner_id is the numeric id from `gh api users/<owner> --jq .id`, not the name."
  }
}

variable "github_repository_id" {
  description = "Immutable numeric id of the repository."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id is the numeric id from `gh api repos/<owner>/<repo> --jq .id`, not the name."
  }
}

variable "monthly_budget_usd" {
  description = <<-EOT
    The threshold at which the budget action detaches the deploy role's permissions.

    **In USD, because AWS Budgets refuses anything else.** `limit_unit = "EUR"` is accepted by
    the provider schema and rejected by the API at apply time — `EUR is not in the supported
    unit set: [USD]` — which is the class of error `terraform validate` cannot see and only a
    plan or an apply against the real account will surface.

    `CLAUDE.md` states the design target as **under €100 for one full capture with teardown**,
    and that stays the design target: it decides what may be built, and
    `scripts/check_cost_envelope.py` enforces it against the design. This variable is a
    different quantity and confusing the two is what made it wrong. It is a **monthly ceiling
    over every capture, apply and idle hour the account runs in a calendar month**, and one
    month of this project has held roughly fifteen captures and twenty applies.

    **Raised from 110 to 250 on 2026-08-20, after the action fired.** It fired correctly: the
    tagged August spend reached USD 115.64 against a 110 ceiling, the deploy role was detached,
    and the next deploy died on `ecr:GetAuthorizationToken ... explicit deny`. Nothing had run
    away — that figure is the whole nine-day exercise, accumulating against a limit set as
    though it bounded a single capture. A ceiling that a normal month of work crosses is a
    ceiling that will be reversed by hand every time, and a control routinely waved through is
    not a control.

    250 is chosen for headroom rather than derived: a month of this project's heaviest work has
    measured USD 115.64, so the ceiling now sits at roughly twice a known-bad month. A ceiling
    is only wrong in one direction — too tight and it detaches the deploy role in the middle of
    a capture, which also blocks the `stop` job that shuts down the three things that bill.

    No exchange rate is recorded here, because a rate written down is a rate that goes stale
    silently.
  EOT
  type        = number
  default     = 250
}

# `deploy_environments` used to be a variable here, defaulting to ["deploy", "destroy"].
#
# It is now written into `local.trusted_subjects` in oidc.tf instead. The values have to equal
# the `environment:` lines in `deploy.yml` and `destroy.yml` exactly — a variable is a knob that
# silently breaks federation when turned, and the resulting `AssumeRoleWithWebIdentity` denial
# is the same message for a wrong repository, a wrong audience and a missing provider. A setting
# whose only correct value is the one already written elsewhere is not a setting.

variable "create_oidc_provider" {
  description = <<-EOT
    Create the GitHub OIDC provider, or reference the one that is already there.

    An account holds at most one IAM OIDC provider per issuer URL, and this portfolio has more
    than one repository that deploys into an account. Creating a second is not a warning, it
    is `EntityAlreadyExists` partway through an apply, at which point half the layer is up.
    Set this to false where another project got there first.
  EOT
  type        = bool
  default     = true
}

variable "github_oidc_thumbprints" {
  description = <<-EOT
    Certificate thumbprints for the GitHub OIDC endpoint.

    Empty by default. AWS verifies GitHub's OIDC endpoint against its own trust store rather
    than against a thumbprint supplied here, and a pinned thumbprint is a value that rotates
    without telling you and breaks every deploy on the day it does. Set it only if a policy
    requires pinning, and then own the rotation.
  EOT
  type        = list(string)
  default     = []
}

variable "state_retention_days" {
  description = "How long a superseded state version is kept. State history is how a bad apply is understood after the fact."
  type        = number
  default     = 90

  validation {
    condition     = var.state_retention_days >= 30
    error_message = "Keep at least 30 days of state history; a shorter window loses the record of the apply that caused the incident."
  }
}
