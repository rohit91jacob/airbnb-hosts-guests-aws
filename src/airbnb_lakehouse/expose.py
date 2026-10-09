"""Expose Iceberg tables to DuckDB (for dbt-duckdb and the data-quality gates).

Locally dbt runs on DuckDB. Before each dbt run we (re)create `bronze.<table>` in the DuckDB
warehouse as a view over `iceberg_scan('<metadata.json>')`, pinned to the Iceberg snapshot
that was current when the run started: one dbt run sees one consistent version of every
bronze table even if a load commits mid-run (the isolation Athena gets from Glue).

`AIRBNB_BRONZE_EXPOSE=table` copies the data instead (streamed in Arrow batches), for machines
where the DuckDB `iceberg` extension cannot be installed.

On AWS (`AIRBNB_CATALOG=glue`) dbt runs on Athena instead, and this module is only used by the
DQ gates: an ephemeral DuckDB in the ECS task reads the Glue-registered Iceberg tables from S3
(httpfs + the task role's credentials) under the same schema names, so the same check SQL runs
in both places. That AWS path is exercised against moto in tests, not against real AWS.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import duckdb
from pyiceberg.catalog import Catalog

from airbnb_lakehouse.bronze import BRONZE_TABLES
from airbnb_lakehouse.catalog import BRONZE, get_catalog
from airbnb_lakehouse.config import Settings

log = logging.getLogger(__name__)


def _load_ext(con: duckdb.DuckDBPyConnection, name: str) -> None:
    try:
        con.execute(f"LOAD {name}")
    except duckdb.Error:
        con.execute(f"INSTALL {name}")
        con.execute(f"LOAD {name}")


def connect(settings: Settings, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    settings.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = duckdb.connect(str(settings.duckdb_path), read_only=read_only)
    except duckdb.ConnectionException:
        # Inside `airbnb run` dbt-duckdb (in-process) may still hold the file open read-write;
        # DuckDB then refuses a second handle with a different configuration. Reuse its mode.
        if not read_only:
            raise
        con = duckdb.connect(str(settings.duckdb_path))
    _load_ext(con, "iceberg")
    if settings.lake_on_s3:
        _s3_secret(con, settings)
    return con


def _s3_secret(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    _load_ext(con, "httpfs")
    parts = [f"TYPE s3, REGION '{settings.aws_region}'"]
    if settings.s3_endpoint_url:  # moto / LocalStack
        u = urlparse(settings.s3_endpoint_url)
        parts += [
            f"ENDPOINT '{u.netloc}'",
            "URL_STYLE 'path'",
            f"USE_SSL {str(u.scheme == 'https').lower()}",
        ]
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        parts += [
            f"KEY_ID '{os.environ['AWS_ACCESS_KEY_ID']}'",
            f"SECRET '{os.environ.get('AWS_SECRET_ACCESS_KEY', '')}'",
        ]
        if os.environ.get("AWS_SESSION_TOKEN"):
            parts.append(f"SESSION_TOKEN '{os.environ['AWS_SESSION_TOKEN']}'")
    else:
        parts.append("PROVIDER credential_chain")
    con.execute(f"CREATE OR REPLACE SECRET lake ({', '.join(parts)})")


def _view(con: duckdb.DuckDBPyConnection, schema: str, name: str, tbl, mode: str) -> None:
    if mode == "table":
        reader = tbl.scan().to_arrow_batch_reader()
        con.register("_batches", reader)
        con.execute(f"CREATE OR REPLACE TABLE {schema}.{name} AS SELECT * FROM _batches")
        con.unregister("_batches")
    else:
        loc = tbl.metadata_location.replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE VIEW {schema}.{name} AS SELECT * FROM iceberg_scan('{loc}')"
        )


def expose_bronze(settings: Settings, catalog: Catalog) -> dict[str, str]:
    mode = os.environ.get("AIRBNB_BRONZE_EXPOSE", "view")
    con = connect(settings)
    pointers = {}
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        for name in BRONZE_TABLES:
            tbl = catalog.load_table(f"{BRONZE}.{name}")
            pointers[name] = tbl.metadata_location
            _view(con, "bronze", name, tbl, mode)
            log.info("expose: bronze.%s -> %s (%s)", name, tbl.metadata_location, mode)
    finally:
        con.close()
    return pointers


def expose_glue_marts(settings: Settings, catalog: Catalog) -> None:
    """AWS only: map the dbt-athena Glue databases (<prefix>_marts, ...) to DuckDB schemas."""
    prefix = os.environ.get("AIRBNB_GLUE_DB_PREFIX", "airbnb")
    con = connect(settings)
    try:
        for schema in ("marts", "snapshots"):
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            for ident in catalog.list_tables(f"{prefix}_{schema}"):
                _view(con, schema, ident[-1], catalog.load_table(ident), "view")
    finally:
        con.close()


def prepare_for_checks(settings: Settings, layer: str) -> None:
    catalog = get_catalog(settings)
    expose_bronze(settings, catalog)
    if layer == "marts" and settings.catalog == "glue":
        expose_glue_marts(settings, catalog)
