from datetime import date

import duckdb

from airbnb_lakehouse.report.charts import line_chart
from airbnb_lakehouse.verify import compare_dashboards, parse_dashboard_text

# Shape of the visible text on an Inside Airbnb city dashboard (numbers are SYNTHETIC).
DASHBOARD_TEXT = """
Testville Filter by: Testville 1,721 out of 3,208 listings (53.6 % ) Room Type
721 ( 41.9 % ) entire homes/apartments 892 ( 51.8 % ) private rooms 64 ( 3.7 % ) shared rooms
44 ( 2.6 % ) hotel rooms Activity 36 average nights booked S$ 314 price/night S$ 7,287 average income
Licenses 314 ( 18.2 % ) unlicensed 1,061 ( 61.7 % ) licensed 346 ( 20.1 % ) exempt 0 ( 0 % ) pending
1,721 ( 100.0 % ) short-term rentals 0 ( 0.0 % ) longer-term rentals
29 ( 1.7 % ) single listings 1,692 ( 98.3 % ) multi-listings
Inside Airbnb data: September 23, 2026
"""


def test_parse_dashboard_text():
    f = parse_dashboard_text(DASHBOARD_TEXT)
    assert f["filtered_listings"] == 1721 and f["total_listings"] == 3208
    assert (f["entire_homes"], f["private_rooms"], f["shared_rooms"], f["hotel_rooms"]) == (
        721,
        892,
        64,
        44,
    )
    assert (f["avg_nights_booked"], f["avg_price"], f["avg_income"], f["currency"]) == (
        36,
        314,
        7287,
        "S$",
    )
    assert (f["unlicensed"], f["licensed"], f["exempt"], f["pending"]) == (314, 1061, 346, 0)
    assert (f["single_listings"], f["multi_listings"]) == (29, 1692)
    assert f["data_date"] == "2026-09-23"


def test_compare_dashboards_reports_diffs_and_missing_snapshots():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA marts")
    con.execute(
        """CREATE TABLE marts.mart_city_snapshot_summary AS SELECT * FROM (VALUES
           ('t', DATE '2026-09-23', 'all', 3208),
           ('t', DATE '2026-09-23', 'dashboard_default', 1720))
           v(city, snapshot_date, scope, listings)"""
    )
    rows = compare_dashboards(
        con,
        {
            "t": {"data_date": "2026-09-23", "total_listings": 3208, "filtered_listings": 1721},
            "u": {"data_date": "2026-09-23", "total_listings": 1},
        },
    )
    by = {(r["city"], r["metric"]): r for r in rows}
    assert by[("t", "total_listings")]["match"]
    assert by[("t", "filtered_listings")]["diff"] == -1
    assert not by[("u", "(snapshot)")]["match"]


def test_line_chart_has_legend_direct_labels_and_tooltips():
    svg = line_chart(
        [
            {
                "name": "A",
                "slot": "--series-1",
                "points": [(date(2026, 1, 1), 10), (date(2026, 2, 1), 30)],
            },
            {
                "name": "B",
                "slot": "--series-2",
                "points": [(date(2026, 1, 1), 5), (date(2026, 2, 1), 7)],
            },
        ],
        y_label="reviews per month",
    )
    assert svg.count('class="key"') == 2  # legend
    assert svg.count('class="direct"') == 2  # direct labels at line ends
    assert svg.count("data-tip=") == 4  # one hover target per point
    assert "var(--series-1)" in svg
    assert line_chart([], y_label="x").startswith("<p")


def test_failed_live_capture_is_reported_as_stored(tmp_path, monkeypatch):
    """A dashboard that cannot be rendered falls back to the stored capture, and says so."""
    import json
    import subprocess

    from airbnb_lakehouse import verify
    from airbnb_lakehouse.config import load_settings

    stored = tmp_path / "published_dashboards.json"
    stored.write_text(
        json.dumps({"t": {"data_date": "2026-09-23", "captured_at": "2026-10-01T00:00:00+00:00"}}),
        encoding="utf-8",
    )
    con = duckdb.connect()
    con.execute(
        """CREATE SCHEMA marts; CREATE TABLE marts.mart_city_snapshot_summary AS
           SELECT * FROM (VALUES ('t', DATE '2026-09-23', 'all', 3208),
                                 ('u', DATE '2026-09-23', 'all', 10)) v(city, snapshot_date, scope, listings)"""
    )

    def fake_capture(city, timeout=150):
        if city == "t":
            raise subprocess.TimeoutExpired("chrome", timeout)
        return {"data_date": "2026-09-23", "captured_at": "2026-10-09T00:00:00+00:00"}

    monkeypatch.setenv("AIRBNB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(verify, "CAPTURE_PATH", stored)
    monkeypatch.setattr(verify, "connect", lambda *a, **k: con.cursor())
    monkeypatch.setattr(verify, "capture_dashboard", fake_capture)
    monkeypatch.setattr(verify, "compare_published_estimates", lambda c: [])
    monkeypatch.setattr(verify, "compare_review_recount", lambda c: [])
    summary = verify.run_verification(load_settings(), capture=True)["summary"]
    assert summary["cities_captured"] == ["t", "u"]
    assert summary["cities_captured_live"] == ["u"]
    assert summary["cities_from_stored_capture"] == ["t"]
    assert summary["captured_at"]["t"] == "2026-10-01T00:00:00+00:00"
