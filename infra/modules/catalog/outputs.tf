output "database_names" {
  description = "Every Glue database of the stack."
  value       = concat([aws_glue_catalog_database.bronze.name], [for d in aws_glue_catalog_database.dbt : d.name])
}
