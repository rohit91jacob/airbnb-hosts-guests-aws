# Step Functions runs the pipeline (definition: statemachine/pipeline.asl.json, the same file
# `airbnb run` walks locally). EventBridge Scheduler starts it monthly; failures go to SNS.
terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
