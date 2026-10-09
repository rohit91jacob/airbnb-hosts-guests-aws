output "lake_bucket" {
  description = "S3 bucket holding the landing zone and the Iceberg warehouse."
  value       = module.storage.lake_bucket_id
}

output "site_bucket" {
  description = "S3 bucket the Report task publishes the results site to."
  value       = module.storage.site_bucket_id
}

output "glue_databases" {
  description = "Glue databases: bronze (Iceberg via pyiceberg) and one per dbt schema."
  value       = module.catalog.database_names
}

output "athena_workgroup" {
  description = "Athena workgroup dbt runs in (enforced bytes-scanned cutoff)."
  value       = module.athena.workgroup_name
}

output "ecr_repository_url" {
  description = "Push the pipeline image here."
  value       = module.compute.ecr_repository_url
}

output "state_machine_arn" {
  description = "Step Functions pipeline."
  value       = module.orchestration.state_machine_arn
}

output "schedule_name" {
  description = "EventBridge Scheduler schedule that starts the pipeline."
  value       = module.orchestration.schedule_name
}

output "alerts_topic_arn" {
  description = "SNS topic for pipeline failures and budget alerts."
  value       = module.orchestration.alerts_topic_arn
}

output "github_deploy_role_arn" {
  description = "Role the deploy workflow assumes through GitHub OIDC (set it as the AWS_DEPLOY_ROLE_ARN repository variable)."
  value       = module.github_oidc.role_arn
}
