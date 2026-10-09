resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:Project$%s", var.project_tag)]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.notify_emails
    subscriber_sns_topic_arns  = [var.alerts_topic_arn]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.notify_emails
    subscriber_sns_topic_arns  = [var.alerts_topic_arn]
  }
}

resource "aws_cloudwatch_metric_alarm" "athena_scan" {
  alarm_name          = "${var.name}-athena-bytes-scanned"
  alarm_description   = "Athena scanned more data in a day than a normal pipeline run needs"
  namespace           = "AWS/Athena"
  metric_name         = "ProcessedBytes"
  dimensions          = { WorkGroup = var.athena_workgroup }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.athena_daily_bytes
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alerts_topic_arn]
}
