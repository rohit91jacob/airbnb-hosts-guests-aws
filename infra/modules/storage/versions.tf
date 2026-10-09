# Buckets: lake (landing zone + Iceberg warehouse), Athena query results, results site, and
# the access-log bucket the other three log to. All private, versioned, SSE-KMS, TLS-only.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
