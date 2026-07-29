terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.5"
    }
  }

  # backend blocks can't interpolate variables, so this bucket (created by
  # infra/bootstrap) is hardcoded rather than variable-driven
  backend "s3" {
    bucket       = "reelwright-tfstate-386566550838"
    key          = "envs/dev/terraform.tfstate"
    region       = "eu-north-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = local.tags
  }
}

# Bedrock (Nova + Guardrails) is not available in the stack region eu-north-1;
# the planning call and its Guardrail live in var.bedrock_region.
provider "aws" {
  alias  = "bedrock"
  region = var.bedrock_region
  default_tags {
    tags = local.tags
  }
}
