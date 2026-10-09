variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}

variable "account_id" {
  description = "AWS account id."
  type        = string
}

variable "partition" {
  description = "AWS partition."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for logs, ECR and S3 access."
  type        = string
}

variable "log_retention_days" {
  description = "Task log retention."
  type        = number
}

variable "image_tag" {
  description = "Image tag of the initial task definition revision."
  type        = string
}

variable "cpu" {
  description = "Fargate CPU units."
  type        = number
}

variable "memory" {
  description = "Fargate memory (MiB)."
  type        = number
}

variable "lake_bucket_arn" {
  description = "Lake bucket ARN."
  type        = string
}

variable "athena_results_bucket_arn" {
  description = "Athena results bucket ARN."
  type        = string
}

variable "site_bucket_arn" {
  description = "Results-site bucket ARN."
  type        = string
}

variable "site_publish_enabled" {
  description = "Allow the Report task to write the site."
  type        = bool
}

variable "glue_database_names" {
  description = "Glue databases the task may read and write."
  type        = list(string)
}

variable "athena_workgroup_arn" {
  description = "Athena workgroup ARN."
  type        = string
}

variable "environment" {
  description = "Container environment variables."
  type        = map(string)
}
