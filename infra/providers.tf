provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  name = "${var.project}-${var.environment}"

  tags = merge(
    {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Repository  = "github.com/${var.github_repository}"
      DataSource  = "insideairbnb.com (CC BY 4.0)"
      CostCenter  = var.cost_center
    },
    var.extra_tags,
  )
}
