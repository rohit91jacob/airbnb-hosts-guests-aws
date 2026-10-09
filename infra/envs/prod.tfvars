# Example variables for a prod deployment: `terraform plan -var-file=envs/prod.tfvars`.
environment                 = "prod"
region                      = "eu-west-1"
cities                      = ["amsterdam", "austin", "singapore"]
snapshots_per_city          = 4
monthly_budget_usd          = 30
create_github_oidc_provider = false # one provider per account; dev already created it
