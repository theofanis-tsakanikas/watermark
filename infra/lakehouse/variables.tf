variable "project" {
  type    = string
  default = "watermark"
}

variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "expires_at" {
  type        = string
  description = "RFC-3339 instant after which the reaper destroys this layer."

  validation {
    condition     = can(formatdate("YYYY-MM-DD", var.expires_at))
    error_message = "expires_at must be an RFC-3339 timestamp."
  }
}

variable "athena_bytes_scanned_cutoff" {
  type    = number
  default = 10737418240

  description = <<-EOT
    Ten gibibytes per query, after which Athena cancels it.

    A ceiling, not a budget. Athena bills by bytes scanned, and the way that becomes expensive
    is not a large query somebody meant to run — it is a partition predicate that stopped
    pruning after a schema change, silently, turning a scan of one day into a scan of the whole
    table. The cutoff makes that a cancelled query rather than an invoice.
  EOT
}
