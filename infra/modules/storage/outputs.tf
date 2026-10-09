output "lake_bucket_id" {
  description = "Lake bucket name."
  value       = module.lake.id
}

output "lake_bucket_arn" {
  description = "Lake bucket ARN."
  value       = module.lake.arn
}

output "athena_results_bucket_id" {
  description = "Athena results bucket name."
  value       = module.athena_results.id
}

output "athena_results_bucket_arn" {
  description = "Athena results bucket ARN."
  value       = module.athena_results.arn
}

output "site_bucket_id" {
  description = "Results-site bucket name."
  value       = module.site.id
}

output "site_bucket_arn" {
  description = "Results-site bucket ARN."
  value       = module.site.arn
}
