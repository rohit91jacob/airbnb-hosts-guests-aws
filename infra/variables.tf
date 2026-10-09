variable "project" {
  description = "Short project name used in every resource name."
  type        = string
  default     = "airbnb-hosts-guests"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.project))
    error_message = "project must be 3-25 lowercase letters, digits or hyphens."
  }
}

variable "environment" {
  description = "Deployment environment (dev, prod)."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "eu-west-1"
}

variable "cost_center" {
  description = "Value of the CostCenter tag (used by cost allocation reports)."
  type        = string
  default     = "portfolio"
}

variable "extra_tags" {
  description = "Additional tags merged into the provider default tags."
  type        = map(string)
  default     = {}
}

variable "cities" {
  description = "Inside Airbnb city slugs the pipeline tracks."
  type        = list(string)
  default     = ["amsterdam", "austin", "singapore"]
}

variable "snapshots_per_city" {
  description = "How many of the latest quarterly snapshots to keep per city."
  type        = number
  default     = 2
}

variable "image_tag" {
  description = "Tag of the pipeline image in ECR for the initial task definition revision (the deploy workflow registers newer revisions)."
  type        = string
  default     = "bootstrap"
}

variable "task_cpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 2048
}

variable "task_memory" {
  description = "Fargate task memory (MiB). The largest bronze load (a city calendar, ~4M rows) needs about 4 GB."
  type        = number
  default     = 8192
}

variable "schedule_expression" {
  description = "EventBridge Scheduler expression. Inside Airbnb publishes each city quarterly with no fixed day, so a monthly check catches a new snapshot within a month and is a no-op otherwise."
  type        = string
  default     = "cron(0 6 3 * ? *)"
}

variable "schedule_timezone" {
  description = "Time zone of the schedule expression."
  type        = string
  default     = "UTC"
}

variable "athena_bytes_scanned_cutoff" {
  description = "Per-query data usage limit of the Athena workgroup, in bytes (minimum 10 MB)."
  type        = number
  default     = 10737418240 # 10 GiB; a full dbt build on three cities scans well under 1 GiB

  validation {
    condition     = var.athena_bytes_scanned_cutoff >= 10485760
    error_message = "Athena requires a cutoff of at least 10 MB."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget for everything tagged Project=<project>."
  type        = number
  default     = 15
}

variable "budget_alert_emails" {
  description = "Email addresses notified at 80% actual and 100% forecast of the budget."
  type        = list(string)
  default     = []
}

variable "alert_emails" {
  description = "Email addresses subscribed to pipeline failure alerts."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 365
}

variable "vpc_cidr" {
  description = "CIDR of the small VPC the Fargate tasks run in."
  type        = string
  default     = "10.42.0.0/24"
}

variable "github_repository" {
  description = "owner/name of the GitHub repository allowed to deploy through OIDC."
  type        = string
  default     = "rohit91jacob/airbnb-hosts-guests-aws"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub Actions OIDC identity provider (only one may exist per account)."
  type        = bool
  default     = true
}

variable "site_publish_enabled" {
  description = "Let the Report task write the results site to the site bucket."
  type        = bool
  default     = true
}
