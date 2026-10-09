"""Landing -> bronze: typed Apache Iceberg tables.

Each landed CSV is read by DuckDB as text, projected onto a declared column list and cast
with TRY_CAST (Inside Airbnb adds and renames columns over time; a declared contract keeps
bronze stable and turns drift into a visible data-quality signal rather than a crash).

Tables (namespace `bronze`):

| table        | partitioned by          | write mode                                         |
|--------------|-------------------------|----------------------------------------------------|
| listings     | city, snapshot_date     | overwrite the (city, snapshot_date) partition      |
| calendar     | city, snapshot_date     | overwrite the (city, snapshot_date) partition      |
| reviews      | city                    | overwrite the city with the newest snapshot (each   |
|              |                         | reviews file is cumulative, so older copies add     |
|              |                         | nothing but duplicates)                             |
| snapshots    | city, snapshot_date     | overwrite; one row per snapshot with model params   |
| load_audit   | (none)                  | overwrite rows for (city, snapshot_date, table)     |

Every write is an Iceberg overwrite scoped to its partition, so re-running a load replaces
rather than duplicates. Loads are skipped when the audit shows the same source SHA-256 was
already loaded.

Reviewer names are dropped on the way in: no mart needs them.
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass
from datetime import UTC, date, datetime

import duckdb
import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import And, EqualTo

from airbnb_lakehouse.catalog import BRONZE
from airbnb_lakehouse.ingest import file_key, manifest_key, params_key
from airbnb_lakehouse.sources.insideairbnb import Snapshot
from airbnb_lakehouse.storage import ObjectStore

log = logging.getLogger(__name__)
# pyiceberg warns when an overwrite's delete filter matches nothing (every first load).
warnings.filterwarnings("ignore", message="Delete operation did not match any records")

BOOL = "BOOLEAN"

# (source column, bronze column, DuckDB type). Text columns are kept as VARCHAR; `price` stays
# raw text in bronze ("$1,234.00") and is parsed in dbt staging so the parse rule is testable.
LISTINGS_COLUMNS: list[tuple[str, str, str]] = [
    ("id", "listing_id", "BIGINT"),
    ("scrape_id", "scrape_id", "VARCHAR"),
    ("last_scraped", "last_scraped", "DATE"),
    ("source", "scrape_source", "VARCHAR"),
    ("name", "listing_name", "VARCHAR"),
    ("host_id", "host_id", "BIGINT"),
    ("host_name", "host_name", "VARCHAR"),
    ("host_since", "host_since", "DATE"),
    ("host_location", "host_location", "VARCHAR"),
    ("host_response_time", "host_response_time", "VARCHAR"),
    ("host_response_rate", "host_response_rate", "VARCHAR"),
    ("host_acceptance_rate", "host_acceptance_rate", "VARCHAR"),
    ("host_is_superhost", "host_is_superhost", BOOL),
    ("host_listings_count", "host_listings_count", "INTEGER"),
    ("host_total_listings_count", "host_total_listings_count", "INTEGER"),
    ("host_has_profile_pic", "host_has_profile_pic", BOOL),
    ("host_identity_verified", "host_identity_verified", BOOL),
    ("neighbourhood_cleansed", "neighbourhood", "VARCHAR"),
    ("neighbourhood_group_cleansed", "neighbourhood_group", "VARCHAR"),
    ("latitude", "latitude", "DOUBLE"),
    ("longitude", "longitude", "DOUBLE"),
    ("property_type", "property_type", "VARCHAR"),
    ("room_type", "room_type", "VARCHAR"),
    ("accommodates", "accommodates", "INTEGER"),
    ("bathrooms", "bathrooms", "DOUBLE"),
    ("bathrooms_text", "bathrooms_text", "VARCHAR"),
    ("bedrooms", "bedrooms", "INTEGER"),
    ("beds", "beds", "INTEGER"),
    ("price", "price_raw", "VARCHAR"),
    ("price_quote_checkin_date", "price_quote_checkin_date", "DATE"),
    ("price_quote_checkout_date", "price_quote_checkout_date", "DATE"),
    ("price_quote_total_price", "price_quote_total_price", "DOUBLE"),
    ("price_quote_price_per_night", "price_quote_price_per_night", "DOUBLE"),
    ("minimum_nights", "minimum_nights", "INTEGER"),
    ("maximum_nights", "maximum_nights", "INTEGER"),
    ("has_availability", "has_availability", BOOL),
    ("availability_30", "availability_30", "INTEGER"),
    ("availability_60", "availability_60", "INTEGER"),
    ("availability_90", "availability_90", "INTEGER"),
    ("availability_365", "availability_365", "INTEGER"),
    ("availability_eoy", "availability_eoy", "INTEGER"),
    ("calendar_last_scraped", "calendar_last_scraped", "DATE"),
    ("number_of_reviews", "number_of_reviews", "INTEGER"),
    ("number_of_reviews_ltm", "number_of_reviews_ltm", "INTEGER"),
    ("number_of_reviews_l30d", "number_of_reviews_l30d", "INTEGER"),
    ("number_of_reviews_ly", "number_of_reviews_ly", "INTEGER"),
    ("estimated_occupancy_l365d", "iab_estimated_occupancy_l365d", "INTEGER"),
    ("estimated_revenue_l365d", "iab_estimated_revenue_l365d", "DOUBLE"),
    ("first_review", "first_review", "DATE"),
    ("last_review", "last_review", "DATE"),
    ("review_scores_rating", "review_scores_rating", "DOUBLE"),
    ("review_scores_accuracy", "review_scores_accuracy", "DOUBLE"),
    ("review_scores_cleanliness", "review_scores_cleanliness", "DOUBLE"),
    ("review_scores_checkin", "review_scores_checkin", "DOUBLE"),
    ("review_scores_communication", "review_scores_communication", "DOUBLE"),
    ("review_scores_location", "review_scores_location", "DOUBLE"),
    ("review_scores_value", "review_scores_value", "DOUBLE"),
    ("license", "license", "VARCHAR"),
    ("instant_bookable", "instant_bookable", BOOL),
    ("calculated_host_listings_count", "calculated_host_listings_count", "INTEGER"),
    (
        "calculated_host_listings_count_entire_homes",
        "calculated_host_listings_count_entire_homes",
        "INTEGER",
    ),
    (
        "calculated_host_listings_count_private_rooms",
        "calculated_host_listings_count_private_rooms",
        "INTEGER",
    ),
    (
        "calculated_host_listings_count_shared_rooms",
        "calculated_host_listings_count_shared_rooms",
        "INTEGER",
    ),
    ("reviews_per_month", "reviews_per_month", "DOUBLE"),
]

CALENDAR_COLUMNS = [
    ("listing_id", "listing_id", "BIGINT"),
    ("date", "calendar_date", "DATE"),
    ("available", "available", BOOL),
    ("price", "price_raw", "VARCHAR"),
    ("adjusted_price", "adjusted_price_raw", "VARCHAR"),
    ("minimum_nights", "minimum_nights", "INTEGER"),
    ("maximum_nights", "maximum_nights", "INTEGER"),
]

REVIEWS_COLUMNS = [
    ("listing_id", "listing_id", "BIGINT"),
    ("id", "review_id", "BIGINT"),
    ("date", "review_date", "DATE"),
    ("reviewer_id", "reviewer_id", "BIGINT"),
    ("comments", "comments", "VARCHAR"),
]

TABLE_COLUMNS = {
    "listings": LISTINGS_COLUMNS,
    "calendar": CALENDAR_COLUMNS,
    "reviews": REVIEWS_COLUMNS,
}
# Columns whose absence makes a file unusable (error-level DQ in the landing layer).
REQUIRED_SOURCE_COLUMNS = {
    "listings": {"id", "host_id", "room_type", "price", "minimum_nights", "number_of_reviews_ltm"},
    "calendar": {"listing_id", "date", "available"},
    "reviews": {"listing_id", "id", "date"},
}
SORT_KEYS = {
    "listings": "listing_id",
    "calendar": "listing_id, calendar_date",
    "reviews": "listing_id, review_date, review_id",
}


@dataclass
class LoadResult:
    city: str
    snapshot_date: date
    table: str
    rows: int
    skipped: bool
    cast_failures: dict[str, int]
    missing_columns: list[str]
    extra_columns: list[str]


def _cast_expr(src: str, dst: str, typ: str, present: set[str]) -> str:
    if src not in present:
        return f"CAST(NULL AS {typ}) AS {dst}"
    col = f'"{src}"'
    if typ == BOOL:
        return f"CASE lower({col}) WHEN 't' THEN TRUE WHEN 'f' THEN FALSE END AS {dst}"
    if typ == "VARCHAR":
        return f"NULLIF({col}, '') AS {dst}"
    return f"TRY_CAST(NULLIF(trim({col}), '') AS {typ}) AS {dst}"


def _failure_expr(src: str, typ: str, present: set[str]) -> str | None:
    if src not in present or typ == "VARCHAR":
        return None
    col = f'"{src}"'
    if typ == BOOL:
        return f"count(*) FILTER (WHERE {col} <> '' AND lower({col}) NOT IN ('t','f'))"
    return f"count(*) FILTER (WHERE trim({col}) <> '' AND TRY_CAST(trim({col}) AS {typ}) IS NULL)"


def read_typed(
    con: duckdb.DuckDBPyConnection, path: str, table: str, snap: Snapshot
) -> tuple[pa.Table, LoadResult]:
    """Read one landed CSV into a typed Arrow table plus a parse report."""
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE raw_src AS SELECT * FROM read_csv(
            '{path}', header=true, all_varchar=true, quote='"', escape='"',
            max_line_size=20000000, strict_mode=false, parallel=false,
            hive_partitioning=false)"""
    )
    present = {r[0] for r in con.execute("DESCRIBE raw_src").fetchall()}
    cols = TABLE_COLUMNS[table]
    declared = {c[0] for c in cols}
    failures = {}
    fexprs = [(c[1], _failure_expr(c[0], c[2], present)) for c in cols]
    fexprs = [(n, e) for n, e in fexprs if e]
    if fexprs:
        row = con.execute("SELECT " + ", ".join(e for _, e in fexprs) + " FROM raw_src").fetchone()
        failures = {n: int(v) for (n, _), v in zip(fexprs, row, strict=True) if v}
    select = ",\n  ".join(_cast_expr(s, d, t, present) for s, d, t in cols)
    arrow = con.execute(
        f"""SELECT '{snap.city}' AS city, DATE '{snap.snapshot_date.isoformat()}' AS snapshot_date,
          {select}
        FROM raw_src ORDER BY {SORT_KEYS[table]}"""
    ).to_arrow_table()
    con.execute("DROP TABLE raw_src")
    result = LoadResult(
        city=snap.city,
        snapshot_date=snap.snapshot_date,
        table=table,
        rows=arrow.num_rows,
        skipped=False,
        cast_failures=failures,
        missing_columns=sorted(declared - present),
        extra_columns=sorted(present - declared),
    )
    return arrow, result


# --- Iceberg helpers ---------------------------------------------------------------------


def _normalise(arrow: pa.Table) -> pa.Table:
    """Iceberg has no unsigned/large variants we need; keep plain string/int types."""
    fields = []
    for f in arrow.schema:
        t = f.type
        if pa.types.is_large_string(t) or pa.types.is_string_view(t):
            t = pa.string()
        fields.append(pa.field(f.name, t, nullable=True))
    return arrow.cast(pa.schema(fields))


def _table(catalog: Catalog, name: str, arrow: pa.Table, partition_by: tuple[str, ...]):
    ident = f"{BRONZE}.{name}"
    try:
        tbl = catalog.load_table(ident)
    except NoSuchTableError:
        tbl = catalog.create_table(ident, schema=arrow.schema)
        if partition_by:
            with tbl.update_spec() as spec:
                for col in partition_by:
                    spec.add_identity(col)
        with tbl.transaction() as tx:
            tx.set_properties(
                {
                    "write.parquet.compression-codec": "zstd",
                    "comment": f"Inside Airbnb {name}, typed (see bronze.py)",
                }
            )
        return catalog.load_table(ident)
    # Additive schema evolution only (new declared columns); never drops or retypes.
    if set(arrow.schema.names) - set(tbl.schema().column_names):
        with tbl.update_schema() as upd:
            upd.union_by_name(arrow.schema)
        tbl = catalog.load_table(ident)
    return tbl


def _overwrite(
    catalog: Catalog, name: str, arrow: pa.Table, keys: dict, partition_by: tuple[str, ...]
) -> None:
    arrow = _normalise(arrow)
    tbl = _table(catalog, name, arrow, partition_by)
    expr = None
    for k, v in keys.items():
        e = EqualTo(k, v)
        expr = e if expr is None else And(expr, e)
    tbl.overwrite(arrow, overwrite_filter=expr)


def ensure_namespace(catalog: Catalog) -> None:
    catalog.create_namespace_if_not_exists(BRONZE)


def audit_rows(catalog: Catalog) -> list[dict]:
    try:
        return catalog.load_table(f"{BRONZE}.load_audit").scan().to_arrow().to_pylist()
    except NoSuchTableError:
        return []


def _write_audit(catalog: Catalog, res: LoadResult, sha: str) -> None:
    arrow = pa.table(
        {
            "city": [res.city],
            "snapshot_date": pa.array([res.snapshot_date], pa.date32()),
            "table_name": [res.table],
            "rows": pa.array([res.rows], pa.int64()),
            "source_sha256": [sha],
            "cast_failures": [json.dumps(res.cast_failures, sort_keys=True)],
            "missing_columns": [json.dumps(res.missing_columns)],
            "extra_columns": [json.dumps(res.extra_columns)],
            "loaded_at": pa.array([datetime.now(UTC).replace(tzinfo=None)], pa.timestamp("us")),
        }
    )
    _overwrite(
        catalog,
        "load_audit",
        arrow,
        {"city": res.city, "snapshot_date": res.snapshot_date, "table_name": res.table},
        (),
    )


def load_snapshot(
    catalog: Catalog, store: ObjectStore, snap: Snapshot, *, force: bool = False
) -> list[LoadResult]:
    manifest = store.get_json(manifest_key(snap))
    if manifest is None:
        raise FileNotFoundError(f"no landing manifest for {snap.city} {snap.snapshot_date}")
    audit = {(a["city"], a["snapshot_date"], a["table_name"]): a for a in audit_rows(catalog)}
    latest_reviews = {}
    for a in audit.values():
        if a["table_name"] == "reviews":
            latest_reviews[a["city"]] = max(
                latest_reviews.get(a["city"], date.min), a["snapshot_date"]
            )

    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=false")
    results = []
    for table in ("listings", "calendar", "reviews"):
        sha = manifest["files"][table]["sha256"]
        prev = audit.get((snap.city, snap.snapshot_date, table))
        if not force and prev and prev["source_sha256"] == sha:
            log.info("bronze: %s %s %s unchanged, skipping", snap.city, snap.snapshot_date, table)
            results.append(
                LoadResult(snap.city, snap.snapshot_date, table, prev["rows"], True, {}, [], [])
            )
            continue
        if table == "reviews" and latest_reviews.get(snap.city, date.min) > snap.snapshot_date:
            log.info("bronze: reviews for %s already loaded from a newer snapshot", snap.city)
            results.append(LoadResult(snap.city, snap.snapshot_date, table, 0, True, {}, [], []))
            continue
        path = store.local_path(file_key(snap, table)).as_posix()
        arrow, res = read_typed(con, path, table, snap)
        if table == "reviews":
            _overwrite(catalog, "reviews", arrow, {"city": snap.city}, ("city",))
        else:
            _overwrite(
                catalog,
                table,
                arrow,
                {"city": snap.city, "snapshot_date": snap.snapshot_date},
                ("city", "snapshot_date"),
            )
        _write_audit(catalog, res, sha)
        log.info("bronze: %s %s %s -> %d rows", snap.city, snap.snapshot_date, table, res.rows)
        results.append(res)
    con.close()
    _write_snapshot_row(catalog, store, snap, manifest)
    return results


def _write_snapshot_row(
    catalog: Catalog, store: ObjectStore, snap: Snapshot, manifest: dict
) -> None:
    params = store.get_json(params_key(snap.city)) or {}
    files = manifest["files"]
    arrow = pa.table(
        {
            "city": [snap.city],
            "snapshot_date": pa.array([snap.snapshot_date], pa.date32()),
            "city_name": [snap.city_name],
            "country": [snap.country],
            "region": [snap.region],
            "scrape_id": [snap.scrape_id],
            "data_root": [snap.data_root],
            "average_nights_stay": pa.array([params.get("average_nights_stay", 3.0)], pa.float64()),
            "night_per_year_threshold": pa.array(
                [params.get("night_per_year_threshold")], pa.int32()
            ),
            "str_threshold": pa.array([params.get("str_threshold")], pa.int32()),
            "requires_license": pa.array([params.get("requires_license")], pa.bool_()),
            "currency_symbol": [params.get("currency_symbol")],
            "listings_sha256": [files["listings"]["sha256"]],
            "calendar_sha256": [files["calendar"]["sha256"]],
            "reviews_sha256": [files["reviews"]["sha256"]],
            "landed_bytes": pa.array([sum(f["bytes"] for f in files.values())], pa.int64()),
        }
    )
    _overwrite(
        catalog,
        "snapshots",
        arrow,
        {"city": snap.city, "snapshot_date": snap.snapshot_date},
        ("city", "snapshot_date"),
    )


def load_all(
    catalog: Catalog, store: ObjectStore, plan: dict, *, force: bool = False
) -> list[LoadResult]:
    ensure_namespace(catalog)
    out: list[LoadResult] = []
    # Oldest first, so the newest reviews file per city is the one that ends up in bronze.
    for s in sorted(plan["snapshots"], key=lambda s: (s["snapshot_date"], s["city"])):
        out.extend(load_snapshot(catalog, store, Snapshot.from_json(s), force=force))
    return out


BRONZE_TABLES = ("listings", "calendar", "reviews", "snapshots", "load_audit")
