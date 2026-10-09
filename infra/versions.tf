terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State stays local by default: this stack is validated in CI, not applied there.
  # For a real deployment, copy backend.tf.example to backend.tf (S3 state with native locking).
}
