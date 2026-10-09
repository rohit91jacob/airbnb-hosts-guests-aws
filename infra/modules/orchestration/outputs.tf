output "state_machine_arn" {
  description = "Pipeline state machine."
  value       = aws_sfn_state_machine.pipeline.arn
}

output "schedule_name" {
  description = "Schedule name."
  value       = aws_scheduler_schedule.monthly.name
}

output "alerts_topic_arn" {
  description = "Alerts topic."
  value       = aws_sns_topic.alerts.arn
}
