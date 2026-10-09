variable "database_prefix" {
  description = "Prefix of the dbt databases (<prefix>_<schema>), matching generate_schema_name."
  type        = string
}

variable "bronze_database" {
  description = "Glue database for the bronze Iceberg tables."
  type        = string
}

variable "dbt_schemas" {
  description = "dbt custom schemas, one Glue database each."
  type        = list(string)
}

variable "lake_bucket" {
  description = "Lake bucket (database location)."
  type        = string
}
