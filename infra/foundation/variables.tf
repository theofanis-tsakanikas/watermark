variable "project" {
  type        = string
  default     = "watermark"
  description = "Prefix for every resource name."
}

variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "expires_at" {
  type        = string
  description = <<-EOT
    RFC-3339 instant after which the reaper destroys this estate.

    Required, with no default. A default expiry is a default somebody accepts without reading,
    and the whole mechanism exists because the expensive resources in this system — Managed
    Flink KPUs, the Feature Store online store, any real-time endpoint — cost money for as long
    as they exist and nothing about them looks wrong while they do.
  EOT

  validation {
    condition     = can(formatdate("YYYY-MM-DD", var.expires_at))
    error_message = "expires_at must be an RFC-3339 timestamp, e.g. 2026-08-10T18:00:00Z."
  }
}

# `monthly_budget_eur` and `budget_alert_email` used to be here. Both moved to
# `infra/bootstrap`, with the budget and the action they belong to — see the comment at the top
# of `cost.tf`. Removing them from this layer is what makes the move real: a variable left
# behind is one a later apply can set, and two budgets counting the same tag would each disable
# the deploy role on their own schedule.

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "availability_zones" {
  type        = list(string)
  default     = ["eu-central-1a", "eu-central-1b"]
  description = "Two, not three. Managed Flink wants multi-AZ; a third buys resilience this estate will never be alive long enough to need, at the price of a third NAT-free subnet's endpoints."
}

variable "log_retention_days" {
  type    = number
  default = 400

  description = <<-EOT
    How long CloudWatch keeps logs.

    Over a year, which is longer than this estate could ever live and is deliberate anyway: AI
    Act Art. 19 requires a provider to retain a high-risk system's automatically generated logs
    for at least six months, and the curtailment path is argued to be one (docs/REGULATORY.md).
    Choosing a retention shorter than the obligation because the estate is short-lived would be
    designing to the demo rather than to the system.
  EOT

  validation {
    condition     = var.log_retention_days >= 180
    error_message = "AI Act Art. 19 sets a floor of six months for a high-risk system's logs."
  }
}
