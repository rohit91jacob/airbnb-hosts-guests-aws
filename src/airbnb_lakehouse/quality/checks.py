"""Data-quality gates between layers.

Each check counts violating rows (0 = pass). `error` checks gate the pipeline: if any fails,
`airbnb dq` exits with code 3 and the state machine (local runner or Step Functions) stops
before the next layer is built. `warn` checks are recorded and reported but never block.

Results are appended to `ops.dq_results` in the DuckDB warehouse and written to
`<data_dir>/state/dq_<layer>.json` (the results site reads the latter).

Layers
------
landing  manifests present and complete, archives intact, required CSV columns present
bronze   keys unique and non-null, prices parse, calendar coverage, referential sanity
marts    counts reconcile with bronze, SCD2 integrity, model bounds, shares in [0, 1]
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import duckdb

from airbnb_lakehouse.bronze import REQUIRED_SOURCE_COLUMNS
from airbnb_lakehouse.config import Settings
from airbnb_lakehouse.ingest import file_key, manifest_key
from airbnb_lakehouse.sources.insideairbnb import FILES, Snapshot
from airbnb_lakehouse.storage import ObjectStore

log = logging.getLogger(__name__)

DQ_EXIT_CODE = 3


@dataclass
class CheckResult:
    layer: str
    name: str
    severity: str
    description: str
    violations: int
    detail: str = ""
    passed: bool = field(init=False)

    def __post_init__(self) -> None:
        self.passed = self.violations == 0


@dataclass(frozen=True)
class SqlCheck:
    name: str
    severity: str
    description: str
    sql: str  # must return a single integer: the number of violating rows


# --- landing (Python: reads manifests and file headers) ----------------------------------


def _header(path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        line = f.readline()
    import csv

    return next(csv.reader(io.StringIO(line)))


def landing_checks(store: ObjectStore, plan: dict) -> list[CheckResult]:
    out: list[CheckResult] = []
    missing_manifests, incomplete, bad_headers, empty = [], [], [], []
    for s in plan["snapshots"]:
        snap = Snapshot.from_json(s)
        tag = f"{snap.city}@{snap.snapshot_date}"
        manifest = store.get_json(manifest_key(snap))
        if manifest is None:
            missing_manifests.append(tag)
            continue
        for name in FILES:
            meta = manifest["files"].get(name)
            if not meta or not store.exists(file_key(snap, name)):
                incomplete.append(f"{tag}/{name}")
                continue
            if meta.get("uncompressed_bytes", 0) < 100:
                empty.append(f"{tag}/{name}")
            cols = set(_header(store.local_path(file_key(snap, name))))
            missing = REQUIRED_SOURCE_COLUMNS[name] - cols
            if missing:
                bad_headers.append(f"{tag}/{name}: missing {sorted(missing)}")
    out.append(
        CheckResult(
            "landing",
            "manifest_present",
            "error",
            "every planned snapshot has a landing manifest",
            len(missing_manifests),
            ", ".join(missing_manifests[:10]),
        )
    )
    out.append(
        CheckResult(
            "landing",
            "files_complete",
            "error",
            "listings, calendar and reviews landed for every snapshot",
            len(incomplete),
            ", ".join(incomplete[:10]),
        )
    )
    out.append(
        CheckResult(
            "landing",
            "files_not_empty",
            "error",
            "no landed file is (nearly) empty",
            len(empty),
            ", ".join(empty[:10]),
        )
    )
    out.append(
        CheckResult(
            "landing",
            "required_columns_present",
            "error",
            "each CSV header has the columns bronze cannot do without",
            len(bad_headers),
            "; ".join(bad_headers[:10]),
        )
    )
    return out


# --- bronze ------------------------------------------------------------------------------

BRONZE_CHECKS = [
    SqlCheck(
        "listings_present_per_snapshot",
        "error",
        "every landed snapshot has listing rows",
        """SELECT count(*) FROM bronze.snapshots s WHERE NOT EXISTS (
                  SELECT 1 FROM bronze.listings l
                  WHERE l.city = s.city AND l.snapshot_date = s.snapshot_date)""",
    ),
    SqlCheck(
        "calendar_present_per_snapshot",
        "error",
        "every landed snapshot has calendar rows",
        """SELECT count(*) FROM bronze.snapshots s WHERE NOT EXISTS (
                  SELECT 1 FROM bronze.calendar c
                  WHERE c.city = s.city AND c.snapshot_date = s.snapshot_date)""",
    ),
    SqlCheck(
        "listing_id_not_null",
        "error",
        "listing_id is never null",
        "SELECT count(*) FROM bronze.listings WHERE listing_id IS NULL",
    ),
    SqlCheck(
        "listing_id_unique",
        "error",
        "one row per listing per snapshot",
        """SELECT count(*) FROM (SELECT city, snapshot_date, listing_id FROM bronze.listings
                GROUP BY ALL HAVING count(*) > 1)""",
    ),
    SqlCheck(
        "host_id_not_null",
        "error",
        "every listing has a host",
        "SELECT count(*) FROM bronze.listings WHERE host_id IS NULL",
    ),
    SqlCheck(
        "room_type_known",
        "error",
        "room_type is one of the four Airbnb types",
        """SELECT count(*) FROM bronze.listings WHERE room_type IS NULL OR room_type NOT IN
                ('Entire home/apt', 'Private room', 'Shared room', 'Hotel room')""",
    ),
    SqlCheck(
        "price_parses",
        "error",
        "every non-empty price string parses to a number",
        """SELECT count(*) FROM bronze.listings WHERE price_raw IS NOT NULL AND
                TRY_CAST(NULLIF(regexp_replace(price_raw, '[^0-9.]', '', 'g'), '') AS DOUBLE) IS NULL""",
    ),
    SqlCheck(
        "coordinates_valid",
        "error",
        "latitude/longitude are on the globe",
        """SELECT count(*) FROM bronze.listings WHERE latitude NOT BETWEEN -90 AND 90
                OR longitude NOT BETWEEN -180 AND 180 OR latitude IS NULL OR longitude IS NULL""",
    ),
    SqlCheck(
        "calendar_unique",
        "error",
        "one calendar row per listing per night",
        """SELECT count(*) FROM (SELECT city, snapshot_date, listing_id, calendar_date
                FROM bronze.calendar GROUP BY ALL HAVING count(*) > 1)""",
    ),
    SqlCheck(
        "review_unique_per_listing",
        "error",
        "a review appears once per listing",
        """SELECT count(*) FROM (SELECT city, listing_id, review_id FROM bronze.reviews
                GROUP BY ALL HAVING count(*) > 1)""",
    ),
    SqlCheck(
        "required_columns_loaded",
        "error",
        "no required column was missing at load",
        """SELECT count(*) FROM bronze.load_audit WHERE
                (table_name = 'listings' AND (missing_columns LIKE '%"listing_id"%' OR missing_columns LIKE '%"id"%'))""",
    ),
    SqlCheck(
        "cast_failures",
        "warn",
        "typed columns parsed without TRY_CAST failures",
        "SELECT count(*) FROM bronze.load_audit WHERE cast_failures <> '{}'",
    ),
    SqlCheck(
        "calendar_full_year",
        "warn",
        "each listing has 365-366 calendar nights (Airbnb publishes a year ahead)",
        """SELECT count(*) FROM (SELECT city, snapshot_date, listing_id, count(*) n
                FROM bronze.calendar GROUP BY ALL) WHERE n NOT BETWEEN 365 AND 366""",
    ),
    SqlCheck(
        "listing_has_calendar",
        "warn",
        "every listing has calendar rows",
        """SELECT count(*) FROM bronze.listings l WHERE NOT EXISTS (SELECT 1 FROM bronze.calendar c
                WHERE c.city = l.city AND c.snapshot_date = l.snapshot_date AND c.listing_id = l.listing_id)""",
    ),
    SqlCheck(
        "minimum_nights_present",
        "warn",
        "minimum_nights is filled (missing values count as short-term on the dashboard)",
        "SELECT count(*) FROM bronze.listings WHERE minimum_nights IS NULL",
    ),
    SqlCheck(
        "scrape_date_near_snapshot",
        "warn",
        "last_scraped is within 14 days of the snapshot date",
        """SELECT count(*) FROM bronze.listings WHERE last_scraped IS NULL OR
                abs(date_diff('day', snapshot_date, last_scraped)) > 14""",
    ),
]

# --- marts -------------------------------------------------------------------------------

MART_CHECKS = [
    SqlCheck(
        "summary_reconciles_with_bronze",
        "error",
        "listings per city x snapshot are identical in bronze and the summary mart",
        """SELECT count(*) FROM (SELECT city, snapshot_date, count(*) n FROM bronze.listings GROUP BY ALL) b
                FULL OUTER JOIN (SELECT city, snapshot_date, listings n FROM marts.mart_city_snapshot_summary
                WHERE scope = 'all') m USING (city, snapshot_date) WHERE b.n IS DISTINCT FROM m.n""",
    ),
    SqlCheck(
        "host_portfolio_reconciles",
        "error",
        "host portfolio listings equal summary listings",
        """SELECT count(*) FROM marts.mart_host_portfolio h JOIN marts.mart_city_snapshot_summary s
                ON s.city = h.city AND s.snapshot_date = h.snapshot_date AND s.scope = 'all'
                WHERE h.listings <> s.listings OR h.hosts <> s.hosts""",
    ),
    SqlCheck(
        "scd2_single_current_version",
        "error",
        "no listing has two open SCD2 versions",
        """SELECT count(*) FROM (SELECT listing_key FROM snapshots.snap_listing_attributes
                WHERE dbt_valid_to IS NULL GROUP BY 1 HAVING count(*) > 1)""",
    ),
    SqlCheck(
        "scd2_current_matches_latest_snapshot",
        "error",
        "open SCD2 versions = listings in each city's latest snapshot",
        """SELECT count(*) FROM (
                  SELECT city, count(*) n FROM snapshots.snap_listing_attributes
                  WHERE dbt_valid_to IS NULL GROUP BY 1) v
                FULL OUTER JOIN (
                  SELECT l.city, count(*) n FROM bronze.listings l
                  JOIN (SELECT city, max(snapshot_date) d FROM bronze.snapshots GROUP BY 1) m
                    ON m.city = l.city AND m.d = l.snapshot_date GROUP BY 1) b USING (city)
                WHERE v.n IS DISTINCT FROM b.n""",
    ),
    SqlCheck(
        "occupancy_within_model_cap",
        "error",
        "modelled occupancy never exceeds 255 nights (the model cap)",
        "SELECT count(*) FROM marts.fct_listing_snapshot WHERE est_nights_ltm > 255 OR est_nights_ltm < 0",
    ),
    SqlCheck(
        "revenue_non_negative",
        "error",
        "estimated revenue and ADR are non-negative",
        "SELECT count(*) FROM marts.mart_revenue_estimates WHERE est_revenue_ltm < 0 OR est_adr < 0",
    ),
    SqlCheck(
        "shares_in_unit_interval",
        "error",
        "every share column is within [0, 1]",
        """SELECT count(*) FROM marts.mart_city_snapshot_summary WHERE
                entire_home_share NOT BETWEEN 0 AND 1 OR short_term_share NOT BETWEEN 0 AND 1
                OR multi_host_listing_share NOT BETWEEN 0 AND 1 OR unlicensed_share NOT BETWEEN 0 AND 1""",
    ),
    SqlCheck(
        "every_snapshot_in_marts",
        "error",
        "every bronze snapshot reached the marts",
        """SELECT count(*) FROM bronze.snapshots s WHERE NOT EXISTS (
                SELECT 1 FROM marts.mart_city_snapshot_summary m
                WHERE m.city = s.city AND m.snapshot_date = s.snapshot_date)""",
    ),
    SqlCheck(
        "sentiment_has_signal",
        "warn",
        "the lexicon finds a signal in at least 20% of each city's reviews",
        """SELECT count(*) FROM (SELECT city, sum(reviews_with_signal) * 1.0 / sum(reviews) c
                FROM marts.mart_guest_sentiment GROUP BY 1) WHERE c < 0.2""",
    ),
    SqlCheck(
        "model_tracks_published_occupancy",
        "warn",
        "modelled nights within 10% of Inside Airbnb's own estimated_occupancy_l365d for 90% of listings",
        """SELECT count(*) FROM (SELECT city, snapshot_date,
                  avg(CASE WHEN abs(est_nights_ltm - iab_estimated_occupancy_l365d)
                           <= greatest(1, 0.1 * iab_estimated_occupancy_l365d) THEN 1 ELSE 0 END) ok
                FROM marts.fct_listing_snapshot WHERE iab_estimated_occupancy_l365d IS NOT NULL
                GROUP BY ALL) WHERE ok < 0.9""",
    ),
]

SQL_CHECKS: dict[str, list[SqlCheck]] = {"bronze": BRONZE_CHECKS, "marts": MART_CHECKS}


def run_sql_checks(con: duckdb.DuckDBPyConnection, layer: str) -> list[CheckResult]:
    out = []
    for c in SQL_CHECKS[layer]:
        try:
            n = int(con.execute(c.sql).fetchone()[0])
            detail = ""
        except duckdb.Error as exc:  # a broken check is itself an error-level failure
            n, detail = 1, f"check failed to run: {exc}"
        out.append(CheckResult(layer, c.name, c.severity, c.description, n, detail))
    return out


def record(
    con: duckdb.DuckDBPyConnection, settings: Settings, layer: str, results: list[CheckResult]
) -> dict:
    run_id = uuid.uuid4().hex[:12]
    now = datetime.now(UTC).replace(tzinfo=None)
    con.execute("CREATE SCHEMA IF NOT EXISTS ops")
    con.execute(
        """CREATE TABLE IF NOT EXISTS ops.dq_results (run_id VARCHAR, layer VARCHAR, name VARCHAR,
           severity VARCHAR, description VARCHAR, violations BIGINT, passed BOOLEAN, detail VARCHAR,
           checked_at TIMESTAMP)"""
    )
    con.executemany(
        "INSERT INTO ops.dq_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                run_id,
                r.layer,
                r.name,
                r.severity,
                r.description,
                r.violations,
                r.passed,
                r.detail,
                now,
            )
            for r in results
        ],
    )
    summary = {
        "run_id": run_id,
        "layer": layer,
        "checked_at": now.isoformat(timespec="seconds"),
        "errors_failed": sum(1 for r in results if r.severity == "error" and not r.passed),
        "warnings_failed": sum(1 for r in results if r.severity == "warn" and not r.passed),
        "results": [asdict(r) for r in results],
    }
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    (settings.state_dir / f"dq_{layer}.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def run_layer(
    settings: Settings,
    layer: str,
    connect: Callable[[], duckdb.DuckDBPyConnection],
    store: ObjectStore | None = None,
    plan: dict | None = None,
) -> dict:
    con = connect()
    try:
        if layer == "landing":
            if store is None or plan is None:
                raise ValueError("the landing gate needs the object store and the run plan")
            results = landing_checks(store, plan)
        else:
            results = run_sql_checks(con, layer)
        summary = record(con, settings, layer, results)
    finally:
        con.close()
    for r in results:
        level = (
            logging.INFO
            if r.passed
            else (logging.ERROR if r.severity == "error" else logging.WARNING)
        )
        log.log(
            level,
            "dq %-7s %-5s %-40s %s",
            layer,
            r.severity,
            r.name,
            "ok" if r.passed else f"FAILED ({r.violations}) {r.detail}",
        )
    return summary
