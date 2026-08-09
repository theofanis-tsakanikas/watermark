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

variable "github_repo" {
  description = "Repository name. Together with the owner and an environment this is the whole of what the deploy role trusts."
  type        = string
  default     = "watermark"
}

variable "deploy_environments" {
  description = <<-EOT
    GitHub environments allowed to assume the deploy role.

    Every trusted subject names this repository AND one environment, with no wildcard. A
    subject of `repo:owner/repo:*` trusts every branch and every pull request in the
    repository, including one opened by a stranger, which is the difference between OIDC and
    a long-lived key written down in public.
  EOT
  type        = list(string)
  default     = ["deploy", "destroy"]

  validation {
    condition     = length(var.deploy_environments) > 0 && alltrue([for e in var.deploy_environments : can(regex("^[a-z][a-z0-9-]*$", e))])
    error_message = "Name at least one environment, in lower case, with no wildcard characters."
  }
}

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
