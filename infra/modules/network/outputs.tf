output "subnet_ids" {
  description = "Subnets the tasks run in."
  value       = aws_subnet.public[*].id
}

output "task_security_group_id" {
  description = "Security group of the pipeline tasks."
  value       = aws_security_group.task.id
}
