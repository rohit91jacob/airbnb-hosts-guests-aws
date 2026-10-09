locals {
  oidc_url = "token.actions.githubusercontent.com"
  provider_arn = (var.create_provider
    ? aws_iam_openid_connect_provider.github[0].arn
  : "arn:${var.partition}:iam::${var.account_id}:oidc-provider/${local.oidc_url}")
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_provider ? 1 : 0
  url            = "https://${local.oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    # main branch pushes and the protected "aws" environment only
    condition {
      test     = "StringLike"
      variable = "${local.oidc_url}:sub"
      values = [
        "repo:${var.github_repository}:ref:refs/heads/main",
        "repo:${var.github_repository}:environment:aws",
      ]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${var.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # no resource-level permissions for this action
  }
  statement {
    sid = "PushImage"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages",
    ]
    resources = [var.ecr_repository_arn]
  }
  statement {
    sid = "TaskDefinitionRevisions"
    # Register/Describe do not support resource-level permissions.
    actions   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
    resources = ["*"]
  }
  statement {
    sid       = "PassOnlyThePipelineRoles"
    actions   = ["iam:PassRole"]
    resources = var.pass_role_arns
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid       = "StartPipeline"
    actions   = ["states:StartExecution", "states:DescribeExecution"]
    resources = [var.state_machine_arn, "${replace(var.state_machine_arn, ":stateMachine:", ":execution:")}:*"]
  }
  statement {
    sid       = "PublishSite"
    actions   = ["s3:PutObject"]
    resources = ["${var.site_bucket_arn}/${var.site_publish_prefix}*"]
  }
  statement {
    sid       = "UseStackKey"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt", "kms:Encrypt"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy-pipeline"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
