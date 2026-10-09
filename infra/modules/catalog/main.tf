resource "aws_glue_catalog_database" "bronze" {
  name         = var.bronze_database
  description  = "Inside Airbnb bronze: typed Apache Iceberg tables written by the LoadBronze task"
  location_uri = "s3://${var.lake_bucket}/warehouse/bronze.db"
}

resource "aws_glue_catalog_database" "dbt" {
  for_each     = toset(var.dbt_schemas)
  name         = "${var.database_prefix}_${each.key}"
  description  = "dbt-athena schema ${each.key}"
  location_uri = "s3://${var.lake_bucket}/warehouse/dbt/${each.key}"
}
