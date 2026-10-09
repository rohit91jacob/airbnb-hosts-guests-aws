variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "cidr" {
  description = "VPC CIDR (a /24 is plenty)."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for the flow-log group."
  type        = string
}

variable "log_retention_days" {
  description = "Flow-log retention."
  type        = number
}
