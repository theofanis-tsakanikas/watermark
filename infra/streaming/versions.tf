terraform {
  required_version = ">= 1.10"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.7" }
  }
  backend "s3" {
    key          = "streaming/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      "watermark:project"    = var.project
      "watermark:layer"      = "streaming"
      "watermark:managed"    = "terraform"
      "watermark:expires-at" = var.expires_at
    }
  }
}

# Cross-layer references are data sources over the foundation layer's outputs, never a remote
# state read. A remote state read makes one layer's internals another layer's contract, and the
# two can then no longer be applied, destroyed or reasoned about separately.
data "aws_vpc" "main" {
  tags = { Name = "${var.project}-vpc" }
}

data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }
  tags = { Name = "${var.project}-private-*" }
}

data "aws_security_group" "endpoints" {
  name   = "${var.project}-endpoints"
  vpc_id = data.aws_vpc.main.id
}

data "aws_kms_key" "data" {
  key_id = "alias/${var.project}-data"
}

data "aws_kms_key" "logs" {
  key_id = "alias/${var.project}-logs"
}

data "aws_caller_identity" "current" {}
