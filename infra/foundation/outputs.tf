# Cross-layer references are outputs → data sources. Never a remote state read across layers:
# that makes one layer's internals another layer's contract, and the two can then no longer be
# applied, destroyed or reasoned about separately.

output "vpc_id" { value = aws_vpc.main.id }

output "private_subnet_ids" { value = [for subnet in aws_subnet.private : subnet.id] }

output "endpoint_security_group_id" { value = aws_security_group.endpoints.id }

output "data_kms_key_arn" { value = aws_kms_key.data.arn }

output "logs_kms_key_arn" { value = aws_kms_key.logs.arn }

output "subject_root_kms_key_arn" {
  description = "Root of the per-subject key hierarchy. The subject keys themselves are created by the erasure orchestration, not by Terraform — there is one per customer."
  value       = aws_kms_key.subject_root.arn
}

output "lakehouse_bucket" { value = aws_s3_bucket.lakehouse.id }

output "access_logs_bucket" { value = aws_s3_bucket.access_logs.id }
