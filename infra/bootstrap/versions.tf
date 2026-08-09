# The bootstrap layer's own state is LOCAL, and that is not an oversight.
#
# This layer creates the remote backend. It cannot store its state in a bucket it has not
# created yet, so `terraform.tfstate` stays on the laptop that ran it, and `.gitignore` keeps
# it out of the repository. `docs/DAY-ONE.md` records what to do with it.
#
# Everything else in `infra/` uses the S3 backend this layer creates, and is applied only from
# a gated workflow. A layer that can be applied from a laptop is a layer that will drift.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      "watermark:project" = var.project
      "watermark:layer"   = "bootstrap"
      "watermark:managed" = "terraform"

      # Every other layer carries an expiry that the reaper enforces. This one must not: the
      # state bucket and the role CI assumes are what every other layer is destroyed *by*.
      # A reaper that eats the backend leaves an estate nothing can reach, which is the one
      # failure mode worse than paying for an idle estate.
      "watermark:expires-at" = "never"
    }
  }
}
