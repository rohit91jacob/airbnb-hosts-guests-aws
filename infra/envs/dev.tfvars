# Example variables for a dev deployment: `terraform plan -var-file=envs/dev.tfvars`.
environment         = "dev"
region              = "eu-west-1"
cities              = ["amsterdam", "austin", "singapore"]
snapshots_per_city  = 2
monthly_budget_usd  = 15
budget_alert_emails = []
alert_emails        = []
