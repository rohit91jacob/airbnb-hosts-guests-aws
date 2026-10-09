# Cost guardrails: a monthly budget on everything tagged Project=<project> (80% actual and
# 100% forecast alerts) and an alarm on Athena bytes scanned per day. The per-query cutoff in
# the workgroup is the hard limit; these are the early warnings.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
