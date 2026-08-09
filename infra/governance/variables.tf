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
