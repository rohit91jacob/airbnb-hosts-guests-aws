# Developer entrypoints. `make help` lists targets. Settings come from .env when present.
-include .env
export

UV ?= uv
SITE ?= site
FIXTURE_CITIES ?= singapore,amsterdam
FIXTURES_DIR ?= tests/fixtures/insideairbnb

.DEFAULT_GOAL := help
.PHONY: help install lint format test test-unit test-e2e test-aws fixtures discover ingest bronze \
        dq transform run fixtures-run verify capture report sql dbt-parse dbt-build dbt-compile-athena \
        tf-fmt tf-validate tf-lint checkov infra-check aws-smoke precommit clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

install: ## Create the virtualenv (dev + athena groups) and install pre-commit hooks
	$(UV) sync --frozen --all-groups
	$(UV) run pre-commit install

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-fix lint and format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test-unit: ## Fast tests (synthetic vectors, no network)
	$(UV) run pytest tests/unit

test-e2e: ## Whole pipeline on the real-data fixture sample (needs `make fixtures`)
	$(UV) run pytest -m e2e tests/e2e

test-aws: ## AWS code paths on moto (in-process; needs `make fixtures`)
	$(UV) run pytest -m aws tests/aws

test: ## Everything, with coverage
	$(UV) run pytest --cov --cov-report=term

fixtures: ## Build the sampled real-data fixtures from a live download (not committed)
	AIRBNB_SOURCE=insideairbnb $(UV) run airbnb fixtures --cities $(FIXTURE_CITIES) --out $(FIXTURES_DIR)

discover: ## List Inside Airbnb snapshots and write the run plan
	$(UV) run airbnb discover

ingest: ## Land planned snapshots in the (emulated) S3 landing zone
	$(UV) run airbnb ingest

bronze: ## Load landed files into Iceberg bronze tables
	$(UV) run airbnb bronze

dq: ## Run all three data-quality gates
	$(UV) run airbnb dq --layer landing
	$(UV) run airbnb dq --layer bronze
	$(UV) run airbnb dq --layer marts

transform: ## dbt seeds, SCD2 replay rounds, build + tests
	$(UV) run airbnb transform

run: ## Whole pipeline on live data, walking the Step Functions definition
	$(UV) run airbnb run --site $(SITE)

fixtures-run: ## Whole pipeline on the fixture sample
	AIRBNB_SOURCE=fixtures $(UV) run airbnb run --site $(SITE)

verify: ## Reconcile marts with the stored Inside Airbnb dashboard captures
	$(UV) run airbnb verify

capture: ## Re-capture the live city dashboards (headless Chrome) and reconcile
	$(UV) run airbnb verify --capture

report: ## Write the static results site to ./site (open site/index.html)
	$(UV) run airbnb report --out $(SITE)

sql: ## Example query: host structure of every snapshot
	$(UV) run airbnb sql "SELECT city, snapshot_date, listings, hosts, round(multi_listing_host_listing_share, 3) AS multi_share, round(superhost_share_of_hosts, 3) AS superhost_share FROM marts.mart_host_portfolio ORDER BY 1, 2"

dbt-parse: ## Parse the dbt project (no warehouse needed)
	cd dbt && $(UV) run dbt parse --profiles-dir . --target ci

dbt-build: ## dbt build on the existing warehouse (after `make bronze` + `airbnb expose`)
	$(UV) run airbnb expose
	cd dbt && $(UV) run dbt build --profiles-dir . --target $${DBT_TARGET:-dev}

dbt-compile-athena: ## Compile every model for the Athena target (offline, dummy credentials)
	cd dbt && AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing AWS_REGION=eu-west-1 \
	  AIRBNB_BRONZE_NAMESPACE=airbnb_hosts_guests_dev_bronze AIRBNB_GLUE_DB_PREFIX=airbnb_hosts_guests_dev \
	  $(UV) run --group athena dbt compile --profiles-dir . --target athena --target-path target-athena \
	  --no-introspect --no-populate-cache --vars '{as_of_round: "2026-01-01"}'

tf-fmt: ## terraform fmt -check
	terraform -chdir=infra fmt -check -recursive

tf-validate: ## terraform init -backend=false + validate
	terraform -chdir=infra init -backend=false -input=false
	terraform -chdir=infra validate

tf-lint: ## tflint (terraform + aws rulesets)
	cd infra && tflint --init && tflint --recursive --config "$$(pwd)/.tflint.hcl"

checkov: ## checkov on the Terraform (every skip is justified inline)
	uvx --from checkov checkov -d infra --framework terraform --compact --quiet

infra-check: tf-fmt tf-validate tf-lint checkov ## All infrastructure checks

aws-smoke: ## AWS code paths against moto in-process (or AWS_ENDPOINT_URL)
	$(UV) run airbnb aws-smoke

precommit: ## pre-commit on every file
	$(UV) run pre-commit run --all-files

clean: ## Remove local caches (not data)
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov dbt/target dbt/target-athena dbt/logs
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
