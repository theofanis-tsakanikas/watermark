# Applied only from a gated workflow. `infra/bootstrap/` created the backend this reads and the
# role that assumes it; nothing here can be applied from a laptop, and that is the point — a
# layer that can be is a layer that will drift.
#
# The backend key is per layer. Two layers sharing one state file is the mistake
# `terraform output backend_configuration` in bootstrap exists to prevent.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # Declared rather than left to implicit resolution. A provider Terraform picks up on its
    # own is a provider whose version is decided by whichever machine ran `init` first.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Filled in by `-backend-config` from the deploy workflow rather than hardcoded: the bucket
  # name carries the account id, and an account id in a repository is an account id in a
  # repository.
  backend "s3" {
    key          = "foundation/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      "watermark:project" = var.project
      "watermark:layer"   = "foundation"
      "watermark:managed" = "terraform"

      # Every resource outside bootstrap carries an expiry, and the reaper enforces it. An
      # estate that outlives its purpose is the only way this project spends money it did not
      # mean to.
      "watermark:expires-at" = var.expires_at
    }
  }
}
