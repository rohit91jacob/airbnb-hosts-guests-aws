variable "name" {
  description = "Workgroup name."
  type        = string
}

variable "results_bucket" {
  description = "Bucket for query results."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for query results."
  type        = string
}

variable "bytes_scanned_cutoff" {
  description = "Per-query data usage control, in bytes."
  type        = number
}
