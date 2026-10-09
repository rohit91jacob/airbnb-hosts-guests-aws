# A small VPC for the Fargate tasks: two public subnets (no NAT gateway, which would cost more
# than the rest of the stack), an S3 gateway endpoint so lake traffic stays on the AWS network,
# flow logs, and a locked-down default security group. Tasks get a public IP only to reach
# insideairbnb.com and the AWS APIs over HTTPS; nothing can connect in.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
