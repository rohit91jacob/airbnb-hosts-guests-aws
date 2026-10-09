# Inside Airbnb batch lakehouse on AWS.
#
#   EventBridge Scheduler (monthly) -> Step Functions -> ECS Fargate tasks (one image, one
#   `airbnb` sub-command per state) -> S3 landing + Iceberg tables in the Glue Data Catalog ->
#   dbt on Athena (workgroup with a bytes-scanned cutoff) -> results site in S3.
#
# This stack is validated (fmt, validate, tflint, checkov) in CI and never applied there.

locals {
  glue_db_prefix = replace(local.name, "-", "_")
  dbt_schemas    = ["staging", "intermediate", "marts", "snapshots", "reference"]
}

module "kms" {
  source = "./modules/kms"

  name       = local.name
  account_id = data.aws_caller_identity.current.account_id
  region     = var.region
  partition  = data.aws_partition.current.partition
}

module "storage" {
  source = "./modules/storage"

  name        = local.name
  account_id  = data.aws_caller_identity.current.account_id
  kms_key_arn = module.kms.key_arn
}

module "catalog" {
  source = "./modules/catalog"

  database_prefix = local.glue_db_prefix
  bronze_database = "${local.glue_db_prefix}_bronze"
  dbt_schemas     = local.dbt_schemas
  lake_bucket     = module.storage.lake_bucket_id
}

module "network" {
  source = "./modules/network"

  name               = local.name
  cidr               = var.vpc_cidr
  kms_key_arn        = module.kms.key_arn
  log_retention_days = var.log_retention_days
}

module "athena" {
  source = "./modules/athena"

  name                 = local.name
  results_bucket       = module.storage.athena_results_bucket_id
  kms_key_arn          = module.kms.key_arn
  bytes_scanned_cutoff = var.athena_bytes_scanned_cutoff
}

module "compute" {
  source = "./modules/compute"

  name               = local.name
  region             = var.region
  account_id         = data.aws_caller_identity.current.account_id
  partition          = data.aws_partition.current.partition
  kms_key_arn        = module.kms.key_arn
  log_retention_days = var.log_retention_days
  image_tag          = var.image_tag
  cpu                = var.task_cpu
  memory             = var.task_memory

  lake_bucket_arn           = module.storage.lake_bucket_arn
  athena_results_bucket_arn = module.storage.athena_results_bucket_arn
  site_bucket_arn           = module.storage.site_bucket_arn
  site_publish_enabled      = var.site_publish_enabled
  glue_database_names       = module.catalog.database_names
  athena_workgroup_arn      = module.athena.workgroup_arn

  environment = {
    AIRBNB_CITIES             = join(",", var.cities)
    AIRBNB_SNAPSHOTS_PER_CITY = tostring(var.snapshots_per_city)
    AIRBNB_STORAGE            = "s3"
    AIRBNB_CATALOG            = "glue"
    AIRBNB_LAKE_BUCKET        = module.storage.lake_bucket_id
    AIRBNB_BRONZE_NAMESPACE   = "${local.glue_db_prefix}_bronze"
    AIRBNB_GLUE_DB_PREFIX     = local.glue_db_prefix
    AIRBNB_ATHENA_WORKGROUP   = module.athena.workgroup_name
    AIRBNB_ATHENA_RESULTS_S3  = "s3://${module.storage.athena_results_bucket_id}/dbt/"
    AIRBNB_ATHENA_DATA_S3     = "s3://${module.storage.lake_bucket_id}/warehouse/dbt/"
    AIRBNB_SITE_BUCKET        = module.storage.site_bucket_id
    AIRBNB_DATA_DIR           = "/tmp/airbnb"
    AIRBNB_LOG_FORMAT         = "json"
    DBT_TARGET                = "athena"
  }
}

module "orchestration" {
  source = "./modules/orchestration"

  name               = local.name
  region             = var.region
  account_id         = data.aws_caller_identity.current.account_id
  partition          = data.aws_partition.current.partition
  kms_key_arn        = module.kms.key_arn
  log_retention_days = var.log_retention_days

  definition_path     = "${path.module}/statemachine/pipeline.asl.json"
  cluster_arn         = module.compute.cluster_arn
  task_family_arn     = module.compute.task_family_arn
  task_role_arns      = [module.compute.task_role_arn, module.compute.execution_role_arn]
  container_name      = module.compute.container_name
  subnet_ids          = module.network.subnet_ids
  security_group_id   = module.network.task_security_group_id
  schedule_expression = var.schedule_expression
  schedule_timezone   = var.schedule_timezone
  alert_emails        = var.alert_emails
}

module "budget" {
  source = "./modules/budget"

  name               = local.name
  project_tag        = var.project
  monthly_limit_usd  = var.monthly_budget_usd
  notify_emails      = var.budget_alert_emails
  alerts_topic_arn   = module.orchestration.alerts_topic_arn
  athena_workgroup   = module.athena.workgroup_name
  athena_daily_bytes = var.athena_bytes_scanned_cutoff * 5
}

module "github_oidc" {
  source = "./modules/github_oidc"

  name                = local.name
  partition           = data.aws_partition.current.partition
  account_id          = data.aws_caller_identity.current.account_id
  github_repository   = var.github_repository
  create_provider     = var.create_github_oidc_provider
  ecr_repository_arn  = module.compute.ecr_repository_arn
  pass_role_arns      = [module.compute.task_role_arn, module.compute.execution_role_arn]
  state_machine_arn   = module.orchestration.state_machine_arn
  kms_key_arn         = module.kms.key_arn
  site_bucket_arn     = module.storage.site_bucket_arn
  site_publish_prefix = "site/"
}
