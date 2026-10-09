output "workgroup_name" {
  description = "Workgroup name."
  value       = aws_athena_workgroup.this.name
}

output "workgroup_arn" {
  description = "Workgroup ARN."
  value       = aws_athena_workgroup.this.arn
}
