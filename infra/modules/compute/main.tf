locals {
  container_name = "pipeline"
  family         = var.name
  scratch        = "/tmp/airbnb"
  # Read-only root filesystem: dbt, DuckDB and the downloads write only to the scratch volume.
  environment = merge(var.environment, {
    HOME                           = local.scratch
    DBT_TARGET_PATH                = "${local.scratch}/dbt-target"
    DBT_LOG_PATH                   = "${local.scratch}/dbt-logs"
    AWS_REGION                     = var.region
    AIRBNB_DATA_DIR                = local.scratch
    PYTHONUNBUFFERED               = "1"
    PYTHONIOENCODING               = "utf-8"
    DBT_SEND_ANONYMOUS_USAGE_STATS = "false"
  })
  glue_db_arns    = [for d in var.glue_database_names : "arn:${var.partition}:glue:${var.region}:${var.account_id}:database/${d}"]
  glue_table_arns = [for d in var.glue_database_names : "arn:${var.partition}:glue:${var.region}:${var.account_id}:table/${d}/*"]
}

# --- registry ------------------------------------------------------------------------------

resource "aws_ecr_repository" "this" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# --- cluster + logs ------------------------------------------------------------------------

resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "task" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

# --- roles ---------------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # GetAuthorizationToken has no resource-level permissions
  }
  statement {
    sid       = "PullImage"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = [aws_ecr_repository.this.arn]
  }
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.task.arn}:*"]
  }
  statement {
    sid       = "DecryptImageLayers"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "pull-and-log"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "ListLake"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [var.lake_bucket_arn, var.athena_results_bucket_arn]
  }
  statement {
    sid = "LakeObjects"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = [
      "${var.lake_bucket_arn}/landing/*",
      "${var.lake_bucket_arn}/warehouse/*",
      "${var.lake_bucket_arn}/state/*",
    ]
  }
  statement {
    sid       = "AthenaResults"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${var.athena_results_bucket_arn}/*"]
  }
  statement {
    sid = "GlueCatalog"
    actions = [
      "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables",
      "glue:CreateTable", "glue:UpdateTable", "glue:DeleteTable", "glue:GetPartition",
      "glue:GetPartitions", "glue:BatchGetPartition", "glue:BatchCreatePartition",
      "glue:BatchDeletePartition", "glue:GetTableVersions", "glue:DeleteTableVersion",
      "glue:BatchDeleteTableVersion",
    ]
    resources = concat(
      ["arn:${var.partition}:glue:${var.region}:${var.account_id}:catalog"],
      local.glue_db_arns,
      local.glue_table_arns,
    )
  }
  statement {
    sid = "AthenaQueries"
    actions = [
      "athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults",
      "athena:StopQueryExecution", "athena:GetWorkGroup",
    ]
    resources = [var.athena_workgroup_arn]
  }
  statement {
    sid       = "UseStackKey"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }
  dynamic "statement" {
    for_each = var.site_publish_enabled ? [1] : []
    content {
      sid       = "PublishSite"
      actions   = ["s3:PutObject"]
      resources = ["${var.site_bucket_arn}/site/*"]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "lake-catalog-athena"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# --- task definition -----------------------------------------------------------------------

resource "aws_ecs_task_definition" "this" {
  family                   = local.family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  ephemeral_storage {
    size_in_gib = 50 # one city snapshot is ~85 MB compressed; calendar expands ~10x in DuckDB
  }

  volume {
    name = "scratch"
  }

  container_definitions = jsonencode([{
    name                   = local.container_name
    image                  = "${aws_ecr_repository.this.repository_url}:${var.image_tag}"
    essential              = true
    command                = ["airbnb", "--help"] # every Step Functions state overrides this
    readonlyRootFilesystem = true
    user                   = "10001:10001"
    environment            = [for k in sort(keys(local.environment)) : { name = k, value = local.environment[k] }]
    mountPoints            = [{ sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }]
    linuxParameters        = { initProcessEnabled = true }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.task.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "pipeline"
      }
    }
  }])
}
