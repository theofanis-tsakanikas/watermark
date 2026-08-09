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
