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
  description = "RFC-3339 instant after which the reaper destroys this layer. No default; see infra/foundation."
  validation {
    condition     = can(formatdate("YYYY-MM-DD", var.expires_at))
    error_message = "expires_at must be an RFC-3339 timestamp."
  }
}

variable "meter_shards" {
  type    = number
  default = 8

  description = <<-EOT
    Shards for the meter stream, in **provisioned** mode.

    Provisioned rather than on-demand, and the reason is in `docs/AWS-CONSTRAINTS.md`: on-demand
    accommodates twice the previous 30-day peak and takes about fifteen minutes to split a
    shard, while this workload's burst is roughly three minutes in every fifteen. On-demand
    would throttle the front of every burst and finish scaling in time for the quiet part.

    Eight shards is sized against the scenario's ~4,000 events/s peak at ~350 bytes a record —
    about 1.4 MB/s, which two shards would carry on paper. The margin is for the retrying
    firmware cohort, which can double a spike, and for the per-key ceiling: a single partition
    key is bounded by one shard's 1 MB/s and 1,000 records/s no matter how many shards exist.
  EOT

  validation {
    condition     = var.meter_shards >= 2
    error_message = "One shard leaves no headroom for the burst and no room for a hot key."
  }
}

variable "telemetry_shards" {
  type        = number
  default     = 4
  description = "Substation telemetry at 1 Hz across 400 substations is small and steady; the shard count is for the consumer's read throughput, not the producer's writes."
}

variable "retention_hours" {
  type    = number
  default = 168

  description = <<-EOT
    Stream retention.

    Seven days, which is longer than it looks like it needs to be and is chosen against the
    recovery drill rather than against the pipeline: restoring from a savepoint is only useful
    if the records the job had not yet processed are still on the stream. A 24-hour retention
    makes a Friday-evening failure unrecoverable by Monday, which is the shape of the outage
    the drill exists for.
  EOT

  validation {
    condition     = var.retention_hours >= 72
    error_message = "The legacy head-end is three days behind; retention shorter than that cannot replay it."
  }
}

variable "flink_runtime" {
  type    = string
  default = "FLINK-1_20"

  description = <<-EOT
    The Managed Flink runtime.

    `scripts/check_flink_versions_agree.py` compares this against the `apache-flink` floor in
    `pyproject.toml`, because an equivalence test (ADR-0003) run against a different Flink than
    the deployed one establishes equivalence with something nobody is running.
  EOT
}

variable "parallelism" {
  type    = number
  default = 4
}

variable "parallelism_per_kpu" {
  type    = number
  default = 2

  description = "Two, not one. The operators do I/O — Kinesis reads, S3 writes — so a KPU spends time blocked, and Managed Flink's own guidance is that a higher value uses the KPU fully when that is true."
}

variable "max_parallelism" {
  type    = number
  default = 128

  description = <<-EOT
    The ceiling for rescaling **while retaining state**.

    Set explicitly, in the first version of the application, because it is the one setting on
    the list that cannot be corrected later: changing it means the application can no longer
    restart from a snapshot taken with the old value. Discovering that during the Phase 4
    recovery drill would be a Phase 1 omission surfacing at the worst moment.
  EOT
}
