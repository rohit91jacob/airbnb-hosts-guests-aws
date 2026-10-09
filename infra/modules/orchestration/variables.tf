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
  description = "KMS key for the topic and logs."
  type        = string
}

variable "log_retention_days" {
  description = "Execution log retention."
  type        = number
}

variable "definition_path" {
  description = "Path of the ASL template."
  type        = string
}

variable "cluster_arn" {
  description = "ECS cluster."
  type        = string
}

variable "task_family_arn" {
  description = "Task definition ARN without revision."
  type        = string
}

variable "task_role_arns" {
  description = "Roles the state machine passes to ECS."
  type        = list(string)
}

variable "container_name" {
  description = "Container to override."
  type        = string
}

variable "subnet_ids" {
  description = "Task subnets."
  type        = list(string)
}

variable "security_group_id" {
  description = "Task security group."
  type        = string
}

variable "schedule_expression" {
  description = "Scheduler expression."
  type        = string
}

variable "schedule_timezone" {
  description = "Scheduler time zone."
  type        = string
}

variable "alert_emails" {
  description = "Failure-alert subscribers."
  type        = list(string)
}
