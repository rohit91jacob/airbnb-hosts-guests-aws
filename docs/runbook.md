# Runbook

How to run, rerun, backfill and debug the pipeline. Locally every AWS service is emulated (see
the README's cloud mapping). The AWS sections describe the deployed shape, which has been
validated but **not applied** to a real account by this project.

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | success | nothing |
| 1 | a step crashed (network, bad config, dbt error) | read the log line `<command> failed: ...`; rerun with `AIRBNB_LOG_LEVEL=DEBUG` for the traceback |
| 3 | an **error-level data-quality gate failed** | see [A data-quality gate failed](#a-data-quality-gate-failed) |

`airbnb run` returns the exit code of the step that failed. Step Functions treats any non-zero
container exit as a task failure, routes to `NotifyFailure` (SNS) and ends in `Failed`.

## Everyday commands

```bash
make run              # live: discover -> land -> DQ -> bronze -> DQ -> dbt (SCD2) -> DQ -> report
make fixtures-run     # the same on the cached real-data sample (no network after `make fixtures`)
make capture          # re-read the live Inside Airbnb dashboards and reconcile
make report           # rebuild ./site from the current warehouse
make sql              # example query; or: uv run airbnb sql "SELECT ..."
```

Steps can also be run one at a time, in the order the state machine uses them:
`airbnb discover`, `ingest`, `dq --layer landing`, `bronze`, `dq --layer bronze`,
`transform`, `dq --layer marts`, `report`.

## Rerunning and idempotency

Every step is safe to rerun:

* **discover** rewrites `state/plan.json`. It is deterministic for a given Inside Airbnb catalog.
* **ingest** skips a snapshot whose manifest exists and whose three files are present. Inside
  Airbnb snapshot files are immutable for a given date. `airbnb ingest --force` re-downloads and
  re-verifies them (gzip integrity, SHA-256).
* **bronze** skips a table load when `bronze.load_audit` already holds the same source SHA-256.
  A forced load (`--force`) is an Iceberg overwrite of exactly that `(city, snapshot_date)`
  partition (reviews: of that city), so it replaces rows and never duplicates them.
* **transform** applies SCD2 rounds only for snapshot dates newer than the last applied round
  (`ops.scd2_rounds`). A rerun with nothing new applies no round, and the marts come out
  identical. The e2e test `test_rerun_is_idempotent` checks this with content hashes of every
  mart and of the SCD2 table.

## Backfilling an older snapshot

Inside Airbnb's free archive covers about the last 12 months. To add an older snapshot (for
example from an archived-data request), put it in the plan and rerun:

1. Raise `AIRBNB_SNAPSHOTS_PER_CITY`, or use a fixture-style directory with `AIRBNB_SOURCE=fixtures`.
2. `airbnb run`. Because a round older than the last applied round has appeared, `transform`
   logs `an older snapshot appeared; rebuilding SCD2 history from scratch` and replays every
   round in date order. The result is the same history as if the snapshots had arrived in order.
   The e2e test `test_full_refresh_rebuild_equals_incremental_history` covers this.

Force a rebuild at any time with `airbnb transform --full-refresh`, or with
`airbnb run --full-refresh`.

## A data-quality gate failed

1. Open `<data_dir>/state/dq_<layer>.json` or the "Data-quality gates" section of the site. The
   `detail` field names the city, snapshot and file or row count.
2. Common causes:
   * **landing / required_columns_present**: Inside Airbnb renamed or dropped a column. Check the
     header of the landed file (`zcat .../listings.csv.gz | head -1`). If the change is real,
     update `LISTINGS_COLUMNS` / `REQUIRED_SOURCE_COLUMNS` in `bronze.py`.
   * **landing / files_complete**: a download was interrupted. Run `airbnb ingest --force`.
   * **bronze / price_parses or room_type_known**: a new format or category. Look at the rows
     with `airbnb sql "SELECT price_raw, count(*) FROM bronze.listings WHERE ... GROUP BY 1"`.
   * **marts / summary_reconciles_with_bronze**: a model filters or duplicates listings. Run
     `cd dbt && uv run dbt test --select mart_city_snapshot_summary+ --profiles-dir .`.
   * **marts / scd2_current_matches_latest_snapshot**: SCD2 history and bronze disagree,
     usually after a manual edit. Run `airbnb transform --full-refresh`.
3. Warning-level checks (`cast_failures`, `calendar_full_year`, `minimum_nights_present`,
   `model_tracks_published_occupancy`, ...) never block. They are listed on the site. Look into
   them when the count jumps between snapshots.

## A city's numbers differ from the Inside Airbnb dashboard

`airbnb verify --capture` re-reads the live dashboards (headless Chrome) and compares about 15
headline numbers per city. See [verification.md](verification.md) for what has to match and why
average income can differ by a few units. If the dashboard already shows a newer snapshot than
the lake, the comparison reports `(snapshot)` as not loaded: run the pipeline first.

## Scheduled refresh fails

`refresh.yml` runs monthly (Inside Airbnb publishes each city about once a quarter). When a
scheduled run fails, the `alert` job opens a "Scheduled refresh is failing" issue, or comments
on it if it is already open.

1. Open the run linked in the issue. The failing step's log has the `airbnb` exit code.
2. Exit 3: follow [A data-quality gate failed](#a-data-quality-gate-failed). Exit 1 during
   discover or ingest is usually Inside Airbnb being unavailable or the site layout changing.
   `sources/insideairbnb.py` falls back from the JSON catalog to scraping the HTML links. If
   both fail, the error says which.
3. Rerun with **Run workflow** (tick "full refresh" only to rebuild the SCD2 history).
4. Close the issue once a run is green.

GitHub disables scheduled workflows after 60 days without repository activity. `keepalive.yml`
re-enables them twice a month.

## On AWS (deployed shape)

* **Start a run by hand:** `aws stepfunctions start-execution --state-machine-arn <arn>`. The
  monthly EventBridge Scheduler schedule does the same.
* **Find a failure:** the execution's failed state shows the ECS task ARN. Its logs are in the
  CloudWatch group `/ecs/<name>`, stream `pipeline/...`. The SNS topic `<name>-alerts` receives
  the failure message, and so does the `ExecutionsFailed` alarm.
* **Cost guardrails:** the Athena workgroup enforces a per-query bytes-scanned cutoff (10 GiB by
  default), a CloudWatch alarm fires on unusual daily scan volume, and an AWS Budget alerts at
  80% actual and 100% forecast of the monthly limit.
* **Deploy a new image:** run the `deploy` workflow. It is disabled until the repository
  variable `AWS_DEPLOY_ROLE_ARN` is set, and authenticates through GitHub OIDC. It pushes the
  image and registers a new task-definition revision. The state machine references the task
  family without a revision, so the next execution uses it.
* **Roll back:** deregister the bad task-definition revision (`aws ecs
  deregister-task-definition`). ECS then runs the previous ACTIVE revision.
