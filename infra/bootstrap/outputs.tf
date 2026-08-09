# Cross-layer references are outputs → data sources. Never a remote state read across layers:
# that makes one layer's internals another layer's contract, and the two then cannot be
# applied, destroyed or reasoned about separately.

output "state_bucket" {
  description = "Backend bucket for every other layer's state."
  value       = aws_s3_bucket.state.id
}

output "state_kms_key_arn" {
  description = "The key the backend encrypts state with."
  value       = aws_kms_key.state.arn
}

output "deploy_role_arn" {
  description = "Role assumed by GitHub Actions. Not transcribed anywhere: the workflows compose it from the account id and the name this layer chose, and `published.tf` writes it to SSM as well so a human can check what the workflow composed."
  value       = aws_iam_role.deploy.arn
}

output "repository_variables" {
  description = "The only two values a person sets by hand. Everything else the deploy needs is published to SSM and resolved after the role is assumed."
  value = {
    AWS_ACCOUNT_ID = data.aws_caller_identity.current.account_id
    AWS_REGION     = var.aws_region
  }
}

output "backend_configuration" {
  description = "Paste into each layer's backend block. Written out because a hand-typed backend key is how two layers end up sharing one state file."
  value       = <<-EOT
    terraform {
      backend "s3" {
        bucket       = "${aws_s3_bucket.state.id}"
        key          = "<layer>/terraform.tfstate"
        region       = "${var.aws_region}"
        encrypt      = true
        kms_key_id   = "${aws_kms_key.state.arn}"
        use_lockfile = true
      }
    }
  EOT
}
