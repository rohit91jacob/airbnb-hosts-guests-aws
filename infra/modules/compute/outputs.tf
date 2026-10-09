output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.this.arn
}

output "task_family" {
  description = "Task definition family."
  value       = aws_ecs_task_definition.this.family
}

output "task_family_arn" {
  description = "Task definition ARN without a revision: ECS runs the latest ACTIVE revision, so the deploy workflow only has to register a new one."
  value       = "arn:${var.partition}:ecs:${var.region}:${var.account_id}:task-definition/${aws_ecs_task_definition.this.family}"
}

output "task_role_arn" {
  description = "Task role ARN."
  value       = aws_iam_role.task.arn
}

output "execution_role_arn" {
  description = "Task execution role ARN."
  value       = aws_iam_role.execution.arn
}

output "container_name" {
  description = "Container name the state machine overrides."
  value       = local.container_name
}

output "ecr_repository_url" {
  description = "ECR repository URL."
  value       = aws_ecr_repository.this.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN."
  value       = aws_ecr_repository.this.arn
}
