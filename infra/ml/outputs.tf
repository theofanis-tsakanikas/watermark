output "feature_group_names" {
  value = { for key, group in aws_sagemaker_feature_group.features : key => group.feature_group_name }
}

output "online_store_enabled" {
  description = "False unless a bounded capture block turned it on. It bills continuously for as long as the feature group exists, read or not."
  value       = var.online_store_enabled
}

output "model_package_groups" {
  value = {
    curtailment_forecast = aws_sagemaker_model_package_group.curtailment_forecast.model_package_group_name
    meter_anomaly        = aws_sagemaker_model_package_group.meter_anomaly.model_package_group_name
  }
}

output "training_role_arn" { value = aws_iam_role.training.arn }
