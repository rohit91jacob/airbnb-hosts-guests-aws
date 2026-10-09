# Athena workgroup dbt runs in. The workgroup configuration is enforced, so clients cannot
# override the result location, the encryption or the per-query bytes-scanned cutoff.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
