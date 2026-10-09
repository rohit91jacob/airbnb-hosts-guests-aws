# One customer-managed key for the stack: S3 (SSE-KMS), CloudWatch Logs, SNS, ECR, Athena results.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
