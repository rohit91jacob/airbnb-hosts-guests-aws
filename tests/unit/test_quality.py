import duckdb

from airbnb_lakehouse.quality import checks


def _con():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA bronze")
    con.execute(
        """CREATE TABLE bronze.listings AS SELECT * FROM (VALUES
           ('a', DATE '2026-01-01', 1, 10, 'Entire home/apt', '$10', 1.0, 1.0, 2, DATE '2026-01-01'),
           ('a', DATE '2026-01-01', 1, 10, 'Castle', 'n/a', 95.0, 1.0, NULL, DATE '2025-01-01'),
           ('a', DATE '2026-01-01', 2, NULL, 'Private room', NULL, 1.0, 1.0, 1, DATE '2026-01-02'))
           t(city, snapshot_date, listing_id, host_id, room_type, price_raw, latitude, longitude,
             minimum_nights, last_scraped)"""
    )
    return con


def test_sql_checks_count_violations():
    con = _con()
    sql = {c.name: c.sql for c in checks.BRONZE_CHECKS}
    n = lambda name: con.execute(sql[name]).fetchone()[0]  # noqa: E731
    assert n("listing_id_unique") == 1
    assert n("host_id_not_null") == 1
    assert n("room_type_known") == 1
    assert n("price_parses") == 1
    assert n("coordinates_valid") == 1
    assert n("minimum_nights_present") == 1
    assert n("scrape_date_near_snapshot") == 1


def test_a_check_that_cannot_run_is_an_error(monkeypatch):
    monkeypatch.setitem(
        checks.SQL_CHECKS,
        "bronze",
        [
            checks.SqlCheck(
                "broken", "error", "refers to a missing table", "SELECT count(*) FROM nope"
            )
        ],
    )
    (res,) = checks.run_sql_checks(duckdb.connect(), "bronze")
    assert not res.passed
    assert "failed to run" in res.detail


def test_record_summarises_errors_and_warnings(tmp_path):
    from airbnb_lakehouse.config import load_settings

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "data_dir": tmp_path})
    results = [
        checks.CheckResult("bronze", "e1", "error", "", 0),
        checks.CheckResult("bronze", "e2", "error", "", 3),
        checks.CheckResult("bronze", "w1", "warn", "", 2),
    ]
    con = duckdb.connect()
    summary = checks.record(con, s, "bronze", results)
    assert (summary["errors_failed"], summary["warnings_failed"]) == (1, 1)
    assert con.execute("SELECT count(*) FROM ops.dq_results").fetchone()[0] == 3
    assert (tmp_path / "state" / "dq_bronze.json").exists()
