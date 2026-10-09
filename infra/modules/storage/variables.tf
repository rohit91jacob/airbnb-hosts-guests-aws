variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "account_id" {
  description = "AWS account id (bucket names are global, so they carry it)."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for SSE-KMS."
  type        = string
}
