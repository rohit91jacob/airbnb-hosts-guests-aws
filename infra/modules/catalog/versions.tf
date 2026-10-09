# Glue Data Catalog databases. Bronze tables are created by pyiceberg (GlueCatalog) at load
# time, and dbt-athena creates the staging/intermediate/marts/snapshots/reference tables. So
# Terraform owns the databases, not the table schemas.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
