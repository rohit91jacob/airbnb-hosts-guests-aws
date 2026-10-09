variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "project_tag" {
  description = "Value of the Project cost-allocation tag."
  type        = string
}

variable "monthly_limit_usd" {
  description = "Monthly budget in USD."
  type        = number
}

variable "notify_emails" {
  description = "Budget alert recipients."
  type        = list(string)
}

variable "alerts_topic_arn" {
  description = "SNS topic that also receives budget alerts."
  type        = string
}

variable "athena_workgroup" {
  description = "Workgroup to watch."
  type        = string
}

variable "athena_daily_bytes" {
  description = "Alarm when Athena scans more than this many bytes in a day."
  type        = number
}
