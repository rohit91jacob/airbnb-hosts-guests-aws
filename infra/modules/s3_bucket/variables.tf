variable "bucket_name" {
  description = "Globally unique bucket name."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for SSE-KMS."
  type        = string
}

variable "log_bucket_id" {
  description = "Bucket that receives the server access logs."
  type        = string
}

variable "log_prefix" {
  description = "Key prefix of this bucket's access logs."
  type        = string
}

variable "noncurrent_days" {
  description = "Days before noncurrent object versions expire."
  type        = number
  default     = 30
}

variable "expire_days" {
  description = "Expire current objects after this many days (null = never)."
  type        = number
  default     = null
}

variable "infrequent_access_prefix" {
  description = "Move objects under this prefix to STANDARD_IA after 30 days (null = none)."
  type        = string
  default     = null
}
