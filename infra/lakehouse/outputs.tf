output "bronze_database" { value = aws_glue_catalog_database.bronze.name }
output "silver_database" { value = aws_glue_catalog_database.silver.name }
output "gold_database" { value = aws_glue_catalog_database.gold.name }
output "athena_workgroup" { value = aws_athena_workgroup.main.name }
output "warehouse_location" { value = local.warehouse }
output "maintenance_jobs" {
  description = "Invoked by the erasure orchestration, which has to be able to wait on them — see ADR-0002."
  value = {
    compaction          = aws_glue_job.compaction.name
    expire_snapshots    = aws_glue_job.expire_snapshots.name
    delete_orphan_files = aws_glue_job.delete_orphan_files.name
  }
}
