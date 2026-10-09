"""End-to-end: walk the Step Functions definition on the real-data fixture sample.

Covers the whole pipeline (discover -> land -> DQ -> Iceberg bronze -> DQ -> dbt with SCD2
replay -> DQ -> report), a rerun that must change nothing (idempotency), a full-refresh rebuild
of the SCD2 history that must equal the incremental one, and a data-quality gate that stops the
run before bronze is built.
"""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import duckdb
import pytest

from airbnb_lakehouse.cli import main
from tests.conftest import AIRBNB_ENV, FIXTURES_DIR, fixtures_available

pytestmark = pytest.mark.e2e

DIGEST_TABLES = [
    "marts.mart_city_snapshot_summary",
    "marts.mart_host_portfolio",
    "marts.mart_revenue_estimates",
    "marts.mart_occupancy_availability",
    "marts.mart_price_change_history",
    "marts.mart_price_change_summary",
    "marts.mart_review_velocity",
    "marts.mart_guest_sentiment",
    "marts.mart_neighbourhood_supply",
    "marts.mart_licence_compliance",
    "marts.dim_listing",
    "snapshots.snap_listing_attributes",
]


def _env(mp: pytest.MonkeyPatch, data: Path) -> None:
    for k in AIRBNB_ENV:
        mp.delenv(k, raising=False)
    mp.setenv("AIRBNB_DATA_DIR", str(data))
    mp.setenv("AIRBNB_SOURCE", "fixtures")
    mp.setenv("AIRBNB_FIXTURES_DIR", str(FIXTURES_DIR))
    mp.setenv("AIRBNB_DUCKDB_PATH", str(data / "warehouse" / "airbnb.duckdb"))
    mp.setenv("DBT_TARGET", "ci")
    mp.setenv("AIRBNB_DBT_THREADS", "2")


def _q(data: Path, sql: str):
    path = str(data / "warehouse" / "airbnb.duckdb")
    try:
        con = duckdb.connect(path, read_only=True)
    except duckdb.ConnectionException:  # dbt (in-process) still holds the file read-write
        con = duckdb.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _digests(data: Path) -> dict[str, tuple]:
    """Order-independent content hash per table.

    Doubles are rounded to 6 significant digits first: DuckDB aggregates in parallel, so an avg() can
    differ in the last bits between two identical runs. dbt_updated_at is the replay clock (a
    snapshot date), so the SCD2 table is deterministic too.
    """
    out = {}
    for t in DIGEST_TABLES:
        schema, name = t.split(".")
        cols = _q(
            data,
            f"""SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema = '{schema}' AND table_name = '{name}' ORDER BY ordinal_position""",
        )
        # 6 significant digits: parallel-sum noise (~1e-13 relative) cannot change them
        sig = (
            'CASE WHEN "{c}" IS NULL OR "{c}" = 0 OR isnan("{c}") THEN "{c}" '
            'ELSE round("{c}", 6 - CAST(ceil(log10(abs("{c}"))) AS INTEGER)) END'
        )
        exprs = [sig.format(c=c) if typ in ("DOUBLE", "FLOAT") else f'"{c}"' for c, typ in cols]
        out[t] = _q(data, f"SELECT count(*), sum(hash({', '.join(exprs)})) FROM {t}")[0]
    return out


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    if not fixtures_available():
        import os

        if os.environ.get("AIRBNB_REQUIRE_FIXTURES") == "1":
            pytest.fail(f"fixture sample missing at {FIXTURES_DIR}; run `make fixtures`")
        pytest.skip("real-data fixture sample not built (run `make fixtures`)")
    data = tmp_path_factory.mktemp("e2e") / "data"
    with pytest.MonkeyPatch.context() as mp:
        _env(mp, data)
        site = data.parent / "site"
        code = main(["run", "--target", "ci", "--site", str(site)])
        first = {
            "code": code,
            "digests": _digests(data) if code == 0 else None,
            "audit": _q(
                data,
                "SELECT table_name, city, snapshot_date, loaded_at FROM bronze.load_audit ORDER BY ALL",
            )
            if code == 0
            else None,
        }
        yield data, site, first, mp


def test_first_run_passes_every_gate(lake):
    data, _site, first, _ = lake
    assert first["code"] == 0
    for layer in ("landing", "bronze", "marts"):
        summary = json.loads((data / "state" / f"dq_{layer}.json").read_text(encoding="utf-8"))
        assert summary["errors_failed"] == 0, summary


def test_layers_reconcile(lake):
    data, *_ = lake
    bronze = {
        (c, str(d)): n
        for c, d, n in _q(
            data, "SELECT city, snapshot_date, count(*) FROM bronze.listings GROUP BY ALL"
        )
    }
    marts = {
        (c, str(d)): n
        for c, d, n in _q(
            data,
            "SELECT city, snapshot_date, listings FROM marts.mart_city_snapshot_summary WHERE scope = 'all'",
        )
    }
    assert bronze == marts
    assert len(bronze) == len(json.loads((FIXTURES_DIR / "catalog.json").read_text())["snapshots"])
    # SCD2: exactly one open version per listing in each city's latest snapshot
    (open_versions,) = _q(
        data, "SELECT count(*) FROM snapshots.snap_listing_attributes WHERE dbt_valid_to IS NULL"
    )[0]
    (latest,) = _q(
        data, "SELECT count(*) FROM marts.fct_listing_snapshot WHERE is_latest_snapshot"
    )[0]
    assert open_versions == latest
    # valid_from is always an Inside Airbnb snapshot date (replay clock), never the run time
    stray = _q(
        data,
        """SELECT count(*) FROM marts.mart_price_change_history h WHERE NOT EXISTS (
             SELECT 1 FROM staging.stg_iab__snapshots s WHERE s.city = h.city AND s.snapshot_date = h.valid_from)""",
    )[0][0]
    assert stray == 0


def test_report_shows_mart_numbers(lake):
    data, site, *_ = lake
    html = (site / "index.html").read_text(encoding="utf-8")
    summary = json.loads((site / "summary.json").read_text(encoding="utf-8"))
    for city, vals in summary["cities"].items():
        (n,) = _q(
            data,
            f"""SELECT listings FROM marts.mart_city_snapshot_summary
                WHERE scope = 'all' AND city = '{city}' AND snapshot_date = DATE '{vals["date"]}'""",
        )[0]
        assert vals["listings"] == n
        assert f"{n:,}" in html
    assert "simple lexicon" in html and "emulated locally" in html
    assert summary["dq"] == {"landing": 0, "bronze": 0, "marts": 0}


def test_rerun_is_idempotent(lake):
    data, site, first, _ = lake
    rounds_before = _q(data, "SELECT round_date FROM ops.scd2_rounds ORDER BY 1")
    assert main(["run", "--target", "ci", "--site", str(site)]) == 0
    # bronze: every load skipped (same source hash), so no Iceberg commit happened
    assert (
        _q(
            data,
            "SELECT table_name, city, snapshot_date, loaded_at FROM bronze.load_audit ORDER BY ALL",
        )
        == first["audit"]
    )
    assert _q(data, "SELECT round_date FROM ops.scd2_rounds ORDER BY 1") == rounds_before
    assert _digests(data) == first["digests"]


def test_full_refresh_rebuild_equals_incremental_history(lake):
    data, _, first, _ = lake
    assert main(["transform", "--target", "ci", "--full-refresh"]) == 0
    assert _digests(data) == first["digests"]


def test_failed_gate_stops_before_bronze(tmp_path, monkeypatch):
    if not fixtures_available():
        pytest.skip("real-data fixture sample not built")
    broken = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_DIR, broken)
    victim = next(broken.glob("*/*/listings.csv.gz"))
    with gzip.open(victim, "wt", encoding="utf-8") as f:
        f.write("id,name\n1,no host or price columns\n")
    data = tmp_path / "data"
    _env(monkeypatch, data)
    monkeypatch.setenv("AIRBNB_FIXTURES_DIR", str(broken))
    assert main(["run", "--target", "ci"]) == 3  # DQ exit code, propagated by the state machine
    summary = json.loads((data / "state" / "dq_landing.json").read_text(encoding="utf-8"))
    failed = {r["name"] for r in summary["results"] if not r["passed"]}
    assert "required_columns_present" in failed
    assert not (data / "lake" / "catalog.db").exists()  # bronze never ran
