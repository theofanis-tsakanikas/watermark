terraform {
  required_version = ">= 1.10"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.7" }
  }
  backend "s3" {
    key          = "governance/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      "watermark:project"    = var.project
      "watermark:layer"      = "governance"
      "watermark:managed"    = "terraform"
      "watermark:expires-at" = var.expires_at
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_kms_key" "data" { key_id = "alias/${var.project}-data" }
data "aws_kms_key" "logs" { key_id = "alias/${var.project}-logs" }
data "aws_kms_key" "subject_root" { key_id = "alias/${var.project}-subject-root" }
data "aws_s3_bucket" "lakehouse" {
  bucket = "${var.project}-lakehouse-${data.aws_caller_identity.current.account_id}"
}
