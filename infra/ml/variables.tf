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

variable "online_store_enabled" {
  type    = bool
  default = false

  description = <<-EOT
    Whether the Feature Store's **online** store is provisioned.

    Off by default, and this is one of the three expensive things `CLAUDE.md` names. The online
    store bills continuously for as long as the feature group exists, whether or not anything
    reads it — so it is stood up inside a deliberate bounded block and destroyed, never left
    running because a variable defaulted to true.

    The offline store is always on: it is S3, it costs almost nothing, and it is what Phase 3
    trains from.
  EOT
}

variable "endpoint_enabled" {
  type        = bool
  default     = false
  description = "Whether a real-time inference endpoint exists. The third expensive thing, and the one that bills per instance-hour with nothing to show for it between requests."
}

variable "promoted_model_name" {
  type    = string
  default = ""

  description = <<-EOT
    The SageMaker model an endpoint serves. Created by a promotion, never by Terraform.

    A Terraform-managed model resource would be a model with no artefact behind it, and an
    endpoint serving it would report healthy while answering from nothing. Empty is refused
    below whenever an endpoint is asked for, so an apply fails at plan time rather than at the
    first inference.
  EOT
}

# Cross-variable validation lives in a check block: a `validation` on one variable cannot see
# another. Terraform evaluates this during plan, which is where the failure belongs.
check "an_endpoint_needs_a_promoted_model" {
  assert {
    condition     = !var.endpoint_enabled || trimspace(var.promoted_model_name) != ""
    error_message = "endpoint_enabled is true and promoted_model_name is empty. An endpoint serving a model that does not exist reports healthy and answers from nothing."
  }
}

variable "processing_image_tag" {
  description = <<-EOT
    The tag of the processing image CI just pushed, which is the git SHA of the commit that
    built it.

    No default, deliberately. A default would be a tag that drifts from the code in this
    checkout, and the pipeline would run last week's container against this week's wheel — two
    runs of one definition that are two different experiments. `deploy.yml` passes the SHA it
    built, and the repository's tags are immutable so the pair cannot come apart later.
  EOT
  type        = string
}

variable "model_monitor_available" {
  type    = bool
  default = false

  description = <<-EOT
    Whether this account may create SageMaker Model Monitor job definitions.

    False, and it is a fact about the account rather than a choice about the design.
    `CreateDataQualityJobDefinition` refuses with "This operation is in maintenance mode and is
    not available to new customers" — the same sentence Clarify gives, recorded with its date in
    docs/AWS-CONSTRAINTS.md. An account that predates the change sets this true and the schedule
    and its job definition apply unchanged.
  EOT
}
