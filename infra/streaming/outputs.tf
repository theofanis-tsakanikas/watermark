output "meter_stream_name" { value = aws_kinesis_stream.meter_readings.name }
output "telemetry_stream_name" { value = aws_kinesis_stream.substation_telemetry.name }
output "flink_application_name" { value = aws_kinesisanalyticsv2_application.watermark.name }
output "schema_registry_arn" { value = aws_glue_registry.meters.arn }
output "device_policy_name" {
  description = "Attached to each device certificate at provisioning. One certificate per device; there is deliberately no fleet-wide credential."
  value       = aws_iot_policy.device.name
}
