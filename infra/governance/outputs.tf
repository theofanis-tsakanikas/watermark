output "erasure_state_machine_arn" {
  description = "Invoked per subject. It refuses to certify unless every leg confirms — which is what claim 6 delivers, rather than a deletion mechanism."
  value       = aws_sfn_state_machine.erasure.arn
}

output "sensitivity_tag" { value = aws_lakeformation_lf_tag.sensitivity.key }
output "purpose_tag" { value = aws_lakeformation_lf_tag.purpose.key }

output "erasure_residual_days" {
  description = "Printed on every certificate. The window in which a model trained before the request is quarantined and retrained; crypto-shredding does not reach weights."
  value       = var.erasure_residual_days
}
