# GitHub Actions -> AWS through OIDC federation (no static keys). The deploy workflow may only:
# push the pipeline image, register a new task-definition revision (passing the two existing
# task roles), start the state machine, and publish the results site.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
