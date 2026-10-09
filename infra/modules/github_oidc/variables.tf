variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "partition" {
  description = "AWS partition."
  type        = string
}

variable "account_id" {
  description = "AWS account id."
  type        = string
}

variable "github_repository" {
  description = "owner/name allowed to assume the role."
  type        = string
}

variable "create_provider" {
  description = "Create the OIDC provider (one per account)."
  type        = bool
}

variable "ecr_repository_arn" {
  description = "Repository the workflow pushes to."
  type        = string
}

variable "pass_role_arns" {
  description = "Task roles the new revision references."
  type        = list(string)
}

variable "state_machine_arn" {
  description = "State machine the workflow may start."
  type        = string
}

variable "kms_key_arn" {
  description = "Stack KMS key (ECR layers, site objects)."
  type        = string
}

variable "site_bucket_arn" {
  description = "Results-site bucket."
  type        = string
}

variable "site_publish_prefix" {
  description = "Key prefix the workflow may write."
  type        = string
}
