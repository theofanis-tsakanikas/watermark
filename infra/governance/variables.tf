variable "project" {
  type    = string
  default = "watermark"
}

variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "expires_at" {
  type = string
  validation {
    condition     = can(formatdate("YYYY-MM-DD", var.expires_at))
    error_message = "expires_at must be an RFC-3339 timestamp."
  }
}

variable "data_steward_principals" {
  type        = list(string)
  default     = []
  description = <<-EOT
    Principals granted Lake Formation access to personal-data columns.

    Empty by default. An access grant that exists because a variable defaulted to something is
    an access grant nobody decided on, and the whole point of tag-based access control is that
    every grant is a written decision.
  EOT
}

variable "erasure_residual_days" {
  type    = number
  default = 30

  description = <<-EOT
    The window printed on an erasure certificate for the leg crypto-shredding cannot reach.

    A model trained before an erasure request keeps the subject's contribution in its weights.
    No key protects that and destroying one does not remove it. That leg is satisfied by
    quarantining the affected model and retraining from the shredded corpus, and this is how
    long that takes — **declared on the face of the certificate** rather than left as a gap
    somebody discovers. Machine unlearning is not claimed. See docs/DECISIONS.md 11.
  EOT

  validation {
    condition     = var.erasure_residual_days > 0
    error_message = "A residual window of zero claims the weights were reached, which they were not."
  }
}

variable "subjects" {
  type = list(string)

  description = <<-EOT
    The data subjects this estate holds, one crypto-shredding key each.

    Supplied by `deploy.yml` from `data/cast.py`, for the same reason the substation list is:
    a second copy in a settings page is a copy that drifts, and a subject with no key is one
    whose erasure request cannot be honoured — which the erasure orchestration discovers at the
    moment somebody has asked to be forgotten.

    No default. An empty list is an estate that holds personal data and can shred none of it.
  EOT

  validation {
    condition     = length(var.subjects) > 0
    error_message = "subjects is empty. Personal data with no per-subject key cannot be erased, and claim 6 would fail at the first request rather than at the plan."
  }
}

variable "feature_store_database" {
  type        = string
  default     = "sagemaker_featurestore"
  description = <<-EOT
    The Glue database SageMaker puts a feature group's offline store in. It is SageMaker's
    default and this project does not create it, which is exactly why it had been granted
    nothing: the erasure's offline-store leg deletes from a table in a database nobody here
    declared.

    A variable rather than a literal so that an account which has moved it can say so, and so
    that the name appears once.
  EOT
}
