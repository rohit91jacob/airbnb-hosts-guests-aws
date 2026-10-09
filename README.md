# Airbnb hosts, guests and rooms: an AWS batch lakehouse

[![ci](https://github.com/rohit91jacob/airbnb-hosts-guests-aws/actions/workflows/ci.yml/badge.svg)](https://github.com/rohit91jacob/airbnb-hosts-guests-aws/actions/workflows/ci.yml)
[![refresh](https://github.com/rohit91jacob/airbnb-hosts-guests-aws/actions/workflows/refresh.yml/badge.svg)](https://github.com/rohit91jacob/airbnb-hosts-guests-aws/actions/workflows/refresh.yml)
[![Live results](https://img.shields.io/badge/live%20results-GitHub%20Pages-0b5cad.svg)](https://rohit91jacob.github.io/airbnb-hosts-guests-aws/)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Data: CC BY 4.0](https://img.shields.io/badge/data-CC%20BY%204.0-green.svg)](DATA_LICENSE.md)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)
![dbt 1.12](https://img.shields.io/badge/dbt-1.12-orange.svg)
![Iceberg](https://img.shields.io/badge/table%20format-Apache%20Iceberg-1d4e89.svg)
![Terraform](https://img.shields.io/badge/terraform-1.16-7b42bc.svg)

A batch data pipeline for the short-term-rental market. It uses real quarterly snapshots from
[Inside Airbnb](https://insideairbnb.com/get-the-data/) for **Amsterdam, Austin and Singapore**,
two snapshots per city. It answers who hosts (single vs multi-listing, Superhosts), how busy
listings are (calendar availability and occupancy, ADR and revenue estimates), how prices and
attributes change between snapshots (SCD Type 2), what guests write (review velocity and a
simple lexicon sentiment), where supply sits (neighbourhoods), and how many listings show a
licence where cities require one.

* **AWS-shaped, emulated locally.** S3 landing zone → Apache Iceberg tables in a Glue-style
  catalog (pyiceberg) → dbt (DuckDB locally; an Athena target compiled in CI) → Step Functions
  state machine started by EventBridge Scheduler. The Terraform for all of it is validated
  (fmt, validate, tflint, checkov) but **was not applied to any AWS account**. Locally the same
  code runs against filesystem and SQLite stand-ins. In tests it runs against moto (S3, Glue,
  Step Functions, Scheduler, SNS, Athena), and a CI job runs it against LocalStack.
* **Real data, verified.** On the 9 October 2026 run, **45 of 45** headline figures on Inside
  Airbnb's own city dashboards match this build exactly: listing counts, room types, average
  nights booked, price and income, licence status, single/multi-listing hosts. The occupancy
  model reproduces Inside Airbnb's published per-listing estimate for **100%** of 49,692
  listing-snapshots. See [verification](docs/verification.md).
* **Production habits.** Idempotent, hash-skipping loads; partition-overwrite Iceberg writes;
  SCD2 history replayed in snapshot order on a pinned clock, so a rebuild from scratch equals the
  incremental history; data-quality gates between layers that stop the run (exit 3); one
  Step Functions definition that both the local runner and AWS execute.
* **Live results.** A monthly GitHub Actions job checks for new snapshots, reruns everything,
  re-reads the dashboards and publishes
  **[rohit91jacob.github.io/airbnb-hosts-guests-aws](https://rohit91jacob.github.io/airbnb-hosts-guests-aws/)**.
  It needs no credentials.

## Architecture

```mermaid
flowchart LR
  IAB[("Inside Airbnb<br/>listings · calendar · reviews<br/>.csv.gz per city per quarter")]
  EB(["EventBridge Scheduler<br/>monthly"]) --> SF

  subgraph SF["Step Functions: statemachine/pipeline.asl.json (local: airbnb run)"]
    direction LR
    D["Discover<br/>catalog JSON + city params"] --> L["Land<br/>sha256 manifest"]
    L --> G1{{"LandingDQ"}} --> B["LoadBronze<br/>typed, partition overwrite"]
    B --> G2{{"BronzeDQ"}} --> T["Transform<br/>dbt seeds → SCD2 rounds → build + tests"]
    T --> G3{{"MartsDQ"}} --> R["Report"]
  end

  IAB --> D
  L --> S3[("S3 landing<br/>landing/insideairbnb/city=/snapshot_date=")]
  S3 --> B
  B --> ICE[("Apache Iceberg bronze<br/>Glue Data Catalog")]
  ICE --> T
  T --> MARTS[("dbt marts + SCD2 snapshot<br/>Athena / DuckDB")]
  MARTS --> R --> SITE[("results site<br/>S3 / GitHub Pages")]
  G1 & G2 & G3 -. "exit 3" .-> SNS(["SNS alert → Fail"])
```

| Component | Code | What it does |
|---|---|---|
| Source discovery | `sources/insideairbnb.py` | Reads the Gatsby static-query JSON behind "Get the data" (572 free snapshots on 9 Oct 2026), and each city dashboard's `page-data` for model parameters (average stay, thresholds). Falls back to scraping HTML links. |
| Landing | `ingest.py`, `storage.py` | Streams each file to a temp file, proves the gzip is complete, records SHA-256, ETag and sizes, and writes the manifest last. Same keys on disk and on S3. Already-landed snapshots are skipped. |
| Bronze | `bronze.py`, `catalog.py` | DuckDB reads each CSV as text and projects a declared contract with `TRY_CAST`. Schema drift and cast failures go to `load_audit` rather than crashing. pyiceberg writes Iceberg with partition overwrites, using a SQLite catalog locally or Glue on AWS. Loads with an unchanged source hash are skipped. |
| Expose | `expose.py` | Pins each bronze table to its current Iceberg snapshot (`iceberg_scan(metadata.json)`) for one consistent dbt run. On AWS the DQ task reads the same tables from S3. |
| Transform | `transform.py`, `dbt/` | Seeds, then one dbt-snapshot round per snapshot date with the SCD2 clock pinned to that date (`macros/snapshot_time.sql`), then `dbt build` with 77 data tests. Only new rounds are applied. An out-of-order snapshot triggers a full replay. |
| Data quality | `quality/checks.py` | 4 landing, 16 bronze and 10 mart checks. Error-level failures exit 3 and stop the state machine. Results go to `ops.dq_results` and the site. |
| Orchestration | `orchestrate.py`, `infra/statemachine/pipeline.asl.json` | The local runner interprets the exact ASL file Terraform deploys: Task → Next, Catch → NotifyFailure → Fail. |
| Verification | `verify.py` | Headless Chrome renders the live dashboards. Their figures are parsed and compared with the marts, alongside the model vs published estimates and a reviews recount. |
| Results site | `report/` | A static HTML page and `summary.json`, with every number read from the warehouse. Publishes to S3 when `AIRBNB_SITE_BUCKET` is set. |
| AWS emulation | `aws/smoke.py` | Runs landing, Iceberg-on-Glue, the DQ-over-S3 path and the orchestration resources against moto or LocalStack. |
| Infrastructure | `infra/` | 10 Terraform modules: KMS, S3, Glue catalog, Athena workgroup, VPC, ECS Fargate + ECR, Step Functions + Scheduler + SNS, budget, GitHub OIDC deploy role, bucket baseline. |

## Tech stack

All Python versions are pinned in `uv.lock`; Terraform providers are pinned in `.terraform.lock.hcl`.

| Layer | Technology | Version |
|---|---|---|
| Language | Python | 3.12 |
| Table format | Apache Iceberg via pyiceberg (SQLite catalog locally, Glue on AWS) | pyiceberg 0.12.0 |
| Engine | DuckDB (+ `iceberg`, `httpfs` extensions) | 1.5.6 |
| Arrow | pyarrow | 26.0.0 |
| Transformations | dbt-core + dbt-duckdb (local/CI), dbt-athena (AWS target) | 1.12.5 / 1.11.0 / 1.11.1 |
| AWS SDK / emulator | boto3, moto (server mode), LocalStack (CI only) | 1.43.110 / 5.2.3 / 2026.09.1 |
| Infrastructure | Terraform, hashicorp/aws provider, tflint (+ aws ruleset), checkov | 1.16.5 / 6.68.0 / 0.64.0 (0.47.0) / 3.3.26 |
| Packaging & quality | uv, hatchling, ruff, pytest, pre-commit, gitleaks, Dependabot | ruff 0.16.10, pytest 9.1.1 |
| Site | Jinja2, inline SVG chart (no JS framework) | 3.1.6 |

## Data source

| | |
|---|---|
| Source | Inside Airbnb, <https://insideairbnb.com/get-the-data/>: `listings.csv.gz`, `calendar.csv.gz`, `reviews.csv.gz` per city per snapshot. Model parameters come from each city dashboard's page data. |
| Cities and snapshots in this build | Amsterdam 2026-06-15, 2026-09-16 · Austin 2026-06-22, 2026-09-17 · Singapore 2026-06-29, 2026-09-23 (set with `AIRBNB_CITIES`, `AIRBNB_SNAPSHOTS_PER_CITY`) |
| Volume (this build) | 332 MB landed (gzip); bronze 49,692 listing rows, 18,236,561 calendar nights, 1,271,388 reviews; 262 MB of Iceberg Parquet |
| Licence | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Inside Airbnb asks that the data not be republished, so **no Inside Airbnb rows are committed**. Fixtures are built from a download. See [DATA_LICENSE.md](DATA_LICENSE.md). |
| Refresh cadence | Inside Airbnb publishes each city about quarterly, on no fixed day, and keeps about 12 months free. The pipeline checks **monthly**. Unchanged snapshots cost one catalog request and no download. |
| Known gaps | `price` is a quote for one upcoming stay, not a list price. The calendar cannot separate booked from blocked nights. Reviews of delisted listings disappear from later files. Older snapshots need an archived-data request. `minimum_nights` is empty for 520 listing-snapshots (a warning-level check). |

## Data model

| Layer | Tables (grain) |
|---|---|
| Landing | three files + `_manifest.json` per city snapshot; city params; run plan |
| Bronze (Iceberg) | `listings` (listing × snapshot), `calendar` (listing × night × snapshot), `reviews` (review × listing), `snapshots`, `load_audit` |
| Staging / intermediate | `stg_iab__*`; `int_listing_snapshots` (occupancy model), `int_calendar_availability`, `int_hosts`, `int_review_sentiment` |
| Snapshot (SCD2) | `snap_listing_attributes`: 45,238 versions of 26,983 listings, 24,931 current |
| Marts | `fct_listing_snapshot`, `dim_listing`, `dim_city_snapshot`, `mart_city_snapshot_summary`, `mart_host_portfolio`, `mart_top_hosts`, `mart_occupancy_availability`, `mart_revenue_estimates`, `mart_price_change_history`, `mart_price_change_summary`, `mart_review_velocity`, `mart_guest_sentiment`, `mart_neighbourhood_supply`, `mart_licence_compliance` |
| Ops | `ops.dq_results`, `ops.scd2_rounds` |

Definitions and every column are in the **[data dictionary](docs/data_dictionary.md)**.

### Occupancy, ADR and revenue model (estimates)

This is Inside Airbnb's "San Francisco model"
([data assumptions](https://insideairbnb.com/data-assumptions/)), reproduced exactly:

* **bookings** in the last 12 months = `number_of_reviews_ltm` ÷ **0.5**. A 50% review rate is
  assumed, between the 72% and 30.5% used in San Francisco studies.
* **nights per booking** = the larger of `minimum_nights` and the city's **average length of
  stay** (Amsterdam 3.9; Austin and Singapore use the 3-night default).
* **nights booked** = bookings × nights per booking, **capped at 255** (70% of the year).
* **revenue** = nights × listed nightly price. **ADR** = revenue ÷ booked nights of listings
  with a price. **RevPAL** = revenue ÷ (priced listings × 365).

The calendar gives a second, independent view: the share of the next 30 nights not available.
That is an upper bound on bookings, because blocked nights look the same as booked ones.

## Results (9 October 2026 run, latest snapshot per city)

| | Amsterdam (2026-09-16) | Austin (2026-09-17) | Singapore (2026-09-23) |
|---|--:|--:|--:|
| Listings (change vs previous snapshot) | 10,612 (+2.3%) | 11,111 (−1.6%) | 3,208 (+3.6%) |
| Hosts | 9,231 | 5,507 | 761 |
| Single-listing hosts (share of hosts) | 93.5% | 74.3% | 54.4% |
| Listings run by multi-listing hosts | 18.7% | 63.2% | 87.1% |
| Superhosts (share of hosts) | 13.5% | 46.7% | 4.7% |
| Next 30 nights unavailable (calendar) | 80.7% | 52.0% | 34.5% |
| Est. occupancy (model) | 14.0% | 21.2% | 6.2% |
| Est. ADR (model) | €255 | $280 | S$197 |
| Median est. revenue per priced listing, 12 months | €10,643 | $10,758 | S$0 |
| Listings with no licence text | 4.4% | 67.9% | 45.9% |
| Priced listings whose price changed since the previous snapshot | 92.6% | 96.6% | 97.5% |

Singapore's median estimated revenue is 0 because most of its listings had no review in the last
12 months. The model infers bookings only from reviews. The "price changed" row mostly
reflects price quotes for different stay dates; see the limitations below. The site also has
neighbourhood supply, review velocity, lexicon sentiment, licence-rule flags and the
largest portfolios.

## Quickstart

### Prerequisites

* Python 3.12 and [uv](https://docs.astral.sh/uv/)
* Internet access to insideairbnb.com (about 330 MB for three cities × two snapshots)
* Optional: Terraform 1.9+ and tflint for `make infra-check`, and Chrome/Chromium for
  `make capture`. Docker is **not** needed locally.

```bash
git clone https://github.com/rohit91jacob/airbnb-hosts-guests-aws.git
cd airbnb-hosts-guests-aws
make install            # uv sync --frozen --all-groups + pre-commit hooks
cp .env.example .env    # optional: every setting has a default

make run                # live: discover -> land -> DQ -> bronze -> DQ -> dbt + SCD2 -> DQ -> report
open site/index.html    # (or start site/index.html on Windows)
make capture            # re-read the live dashboards and reconcile (headless Chrome)
make sql                # query the marts
```

On this machine (a Windows 11 laptop), the six snapshots took **79 s** to download and land.
The rest of the first run took **2 min 33 s**: 74 s for bronze, 61 s for dbt with six SCD2 rounds
and its tests, and seconds for each gate. A rerun with nothing new took **1 min 38 s**. All 18
bronze loads were skipped, no SCD2 round was applied, and dbt rebuilt and re-tested the marts
(PASS=102).

### Without hitting Inside Airbnb every time

```bash
make fixtures           # one download -> a 120-listings-per-city real sample (gitignored)
make fixtures-run       # whole pipeline on the sample, offline
make test               # unit + e2e + AWS-emulator tests
```

### Emulated AWS

```bash
make aws-smoke          # moto in-process: S3 landing, Iceberg on Glue + S3, DQ via DuckDB over S3,
                        # Step Functions definition, Scheduler schedule, SNS, Athena workgroup
AWS_ENDPOINT_URL=http://localhost:4566 uv run airbnb aws-smoke --catalog sql   # against LocalStack
make dbt-compile-athena # every model compiled for Athena (offline, dummy credentials)
make infra-check        # terraform fmt/validate, tflint, checkov
```

## Cloud mapping: local emulator → real service

| Concern | Local / CI (what actually ran) | AWS (Terraform, validated, not applied) |
|---|---|---|
| Landing zone | Filesystem laid out as `s3://<bucket>/<key>`; moto S3; LocalStack S3 | S3 bucket: SSE-KMS, versioning, access logs, lifecycle to IA, TLS-only |
| Table catalog | pyiceberg `SqlCatalog` (SQLite); moto Glue in tests | AWS Glue Data Catalog: one database for bronze and one per dbt schema |
| Table format | Apache Iceberg (Parquet + zstd) on disk or emulated S3 | Apache Iceberg on S3 |
| SQL engine | DuckDB via dbt-duckdb | Amazon Athena (engine v3) via dbt-athena, in a workgroup with an enforced 10 GiB per-query cutoff |
| Compute | `airbnb <step>` in-process | ECS Fargate task (one image, read-only root filesystem, non-root user) |
| Orchestration | `airbnb run` walks `pipeline.asl.json`; moto/LocalStack register it | AWS Step Functions (logging, X-Ray, KMS) |
| Schedule | GitHub Actions cron (monthly) | EventBridge Scheduler (monthly, KMS-encrypted) |
| Alerts | Failure issue opened by `refresh.yml` | SNS topic + `ExecutionsFailed` alarm |
| Results site | GitHub Pages | S3 site bucket (`AIRBNB_SITE_BUCKET`) |
| Secrets / auth | Dummy emulator credentials only | Task roles (least privilege), GitHub OIDC deploy role, no static keys |
| Cost guardrails | n/a | AWS Budget (80% actual / 100% forecast), Athena bytes-scanned cutoff + daily scan alarm |

## Testing and CI

| Suite | What it covers | Local result (9 Oct 2026) |
|---|---|---|
| `tests/unit` (28) | catalog parsing, landing integrity and idempotency, bronze typing, drift and reruns, every DQ check, state-machine routing, CLI exit codes, S3 publish (moto), Terraform ↔ ASL placeholders, verify's live-vs-stored capture reporting | 28 passed |
| `tests/e2e` (6) | whole state machine on the real sample; layer reconciliation; site numbers = marts; **rerun leaves every mart and the SCD2 table byte-identical**; full-refresh SCD2 = incremental; failed gate stops before bronze (exit 3) | 6 passed |
| `tests/aws` (1) | moto: S3 landing, Glue-registered Iceberg, DuckDB reading Iceberg from S3, idempotent reload, Step Functions + Scheduler + SNS + Athena workgroup | 1 passed |
| dbt | 77 data tests (uniqueness, bounds, accepted values, SCD2 integrity, cross-layer reconciliation) | PASS=102 on real data (2 seeds, 22 models, 1 snapshot, 77 tests) |

The full local run (`make test`) took 13 min 7 s on the development laptop: 35 passed, 84% line + branch coverage.

[`ci.yml`](.github/workflows/ci.yml) runs these jobs: lint (pre-commit: ruff, gitleaks,
`terraform fmt`, hygiene), unit tests, a fixture job (download once, cached by snapshot
fingerprint), pipeline + `dbt build` + e2e, dbt compile for Athena, AWS on moto, AWS on
LocalStack, Terraform (fmt, validate, tflint, checkov) and a Docker build. Every action is pinned to a
commit SHA. The LocalStack and Docker jobs cannot run on the development machine (no Docker
there), so they have only run in GitHub Actions, not locally. The LocalStack job needs a
`LOCALSTACK_AUTH_TOKEN` secret for current LocalStack images. Without one it skips with a
visible warning, and the moto job still covers the same code paths.

[`refresh.yml`](.github/workflows/refresh.yml) runs monthly, caches the lake between runs,
publishes the site to Pages and opens an issue on failure. [`keepalive.yml`](.github/workflows/keepalive.yml)
keeps the schedules enabled. [`deploy.yml`](.github/workflows/deploy.yml) is inert unless
`vars.AWS_DEPLOY_ROLE_ARN` is set. It then uses OIDC to push the image and register a new
task-definition revision.

## Cost notes

Nothing here has run on AWS, so these are **estimates from public list prices**, not bills.
For one monthly run of three cities: S3 holds under 1 GB (~$0.02), Fargate runs about 10 task
minutes at 2 vCPU / 8 GB (~$0.03), and Athena scans little. Bronze is 262 MB of Parquet, so even
a build reading every column several times scans a few GB (~$0.01 at $5/TB). Step Functions,
Scheduler and SNS stay inside their free tiers. The fixed costs are the KMS key ($1/month),
CloudWatch Logs and the short-lived public IPv4 addresses. Expect about **$1–3 a month**. The
budget defaults to $15 and alerts long before that. The VPC has no NAT gateway (about $32/month),
for that reason.

## Configuration

Every setting is an environment variable with a working default (`src/airbnb_lakehouse/config.py`,
documented in [`.env.example`](.env.example)). The main ones: `AIRBNB_DATA_DIR`, `AIRBNB_CITIES`,
`AIRBNB_SNAPSHOTS_PER_CITY`, `AIRBNB_SOURCE` (`insideairbnb` | `fixtures`), `AIRBNB_STORAGE`
(`local` | `s3`), `AIRBNB_CATALOG` (`sql` | `glue`), `AWS_ENDPOINT_URL` (emulators) and `DBT_TARGET`
(`dev` | `ci` | `athena`).

## Project structure

```
src/airbnb_lakehouse/
  sources/insideairbnb.py  catalog + dashboard parameters      ingest.py      landing + manifests
  bronze.py                typed Iceberg loads                  catalog.py     SQLite / Glue catalog
  expose.py                Iceberg -> DuckDB (pinned snapshots) transform.py   dbt + SCD2 replay
  quality/checks.py        gates (exit 3)                       orchestrate.py ASL interpreter
  verify.py                published-figure reconciliation      report/        results site
  aws/smoke.py             moto / LocalStack runs               cli.py         `airbnb` CLI
dbt/        staging -> intermediate -> marts, snapshots/ (SCD2), seeds/, tests/, macros/ (DuckDB + Athena)
infra/      main.tf + modules/{kms,storage,s3_bucket,catalog,athena,network,compute,orchestration,budget,github_oidc}
            statemachine/pipeline.asl.json (shared with the local runner)
tests/      unit/ (synthetic vectors)  e2e/ (real sample)  aws/ (moto, LocalStack)
docs/       data_dictionary.md  runbook.md  verification.md  verification/published_dashboards.json
```

## Design decisions and trade-offs

* **One state machine, two executors.** Running the deployed ASL file locally means the step
  order and the gates cannot drift between laptop and cloud. The cost: the local runner supports
  only Task, Succeed and Fail states, and it does not emulate retries.
* **Iceberg written by pyiceberg, read by DuckDB.** There is no Spark: three cities fit easily
  on one machine, and on AWS one Fargate task. Partition overwrites make reloads idempotent.
  Pinning `metadata.json` gives each dbt run a consistent view.
* **SCD2 replay clock.** dbt snapshots normally stamp "now". Here each snapshot date is replayed
  as its own round, with `snapshot_get_time()` overridden, so `valid_from` is when Inside Airbnb
  observed the change. Rebuilds are then deterministic, and the e2e suite checks that.
* **Bronze keeps raw strings where parsing is a business rule.** `price` is parsed in dbt, where
  a test covers the rule. Bronze casts everything else and records failures instead of dropping
  rows.
* **Respect the source.** Nothing is republished, each file is downloaded once, and the
  site shows aggregates without names.

## Limitations

* **Emulated, not deployed.** No AWS account was used. moto and LocalStack check API shapes and
  the data path, not IAM enforcement, service quotas or Athena's real SQL dialect. The Athena
  models are compiled, not executed.
* **Model, not bookings.** Occupancy, revenue and ADR are estimates. They reproduce Inside Airbnb's
  model exactly, but its assumptions (50% review rate, average stay, 70% cap) are not validated
  against real booking data, because none is public.
* **Price is a dated quote.** The listings `price` equals the per-night rate of
  `price_quote_total_price` for one stay. That stay is usually the minimum stay, starting a few
  days after the scrape (median 1–13 days here). Changes between snapshots therefore mix real
  repricing with seasonal and dynamic pricing. The SCD2 history records them faithfully but cannot
  tell the two apart. Singapore's median quoted price rose 59% between the June and September
  snapshots (S$127.53 to S$202.77 over all priced listings, with the same 6-night median quote
  length); among listings whose price changed, the median change was +53%. This build does not
  explain why.
* **Sentiment is a toy.** It counts 138 hand-picked English words with no negation handling, so
  non-English reviews mostly carry no signal. Over the last 12 months of reviews, 64% of
  Amsterdam's and Singapore's (and 94% of Austin's) carry one.
  It is a trend indicator, clearly labelled.
* **Licence flags are indicative.** The rules are simplified and cite their sources. Inside
  Airbnb's licence field shows whether *something* is filled in, not whether it is valid.
* **Two snapshots per city.** The free archive covers about 12 months. Longer histories need an
  archived-data request. The pipeline then backfills by replaying SCD2 from scratch.

## Operations

See the **[runbook](docs/runbook.md)** for exit codes, reruns, backfills, gate failures,
the scheduled refresh and the AWS deploy and rollback path.

## License

Code: [MIT](LICENSE). Data: Inside Airbnb, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Attribution and the no-republishing policy are in [DATA_LICENSE.md](DATA_LICENSE.md). This project
has no connection to Airbnb, Inc. or Inside Airbnb.
