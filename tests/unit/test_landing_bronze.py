"""Landing + bronze on SYNTHETIC test vectors (see tests/conftest.py)."""

from __future__ import annotations

import gzip
import json
from datetime import date

import pytest

from airbnb_lakehouse import bronze, ingest
from airbnb_lakehouse.catalog import get_catalog
from airbnb_lakehouse.config import load_settings
from airbnb_lakehouse.quality.checks import landing_checks
from airbnb_lakehouse.storage import get_store
from tests.conftest import make_snapshot_dir, synthetic_listing, write_synthetic_catalog


@pytest.fixture
def synthetic(clean_env, monkeypatch):
    fx = clean_env / "fixtures"
    make_snapshot_dir(
        fx,
        "testville",
        "2026-01-02",
        [
            synthetic_listing(1, 10),
            synthetic_listing(2, 10, room_type="Private room", price="", host_is_superhost="t"),
            synthetic_listing(3, 11, minimum_nights="bad", license="STR-123"),
        ],
        nights=5,
    )
    make_snapshot_dir(
        fx,
        "testville",
        "2026-04-02",
        [synthetic_listing(1, 10, price="$99.00"), synthetic_listing(4, 12)],
        nights=5,
    )
    write_synthetic_catalog(fx, [("testville", "2026-01-02"), ("testville", "2026-04-02")])
    monkeypatch.setenv("AIRBNB_SOURCE", "fixtures")
    monkeypatch.setenv("AIRBNB_FIXTURES_DIR", str(fx))
    monkeypatch.setenv("AIRBNB_CITIES", "testville")
    s = load_settings()
    store = get_store(s)
    plan = ingest.build_plan(s)
    ingest.write_plan(s, plan, store)
    return s, store, plan


def test_verify_gzip_detects_truncation(tmp_path):
    p = tmp_path / "x.csv.gz"
    with gzip.open(p, "wb") as f:
        f.write(b"a,b\n" * 1000)
    assert ingest.verify_gzip(p) == 4000
    p.write_bytes(p.read_bytes()[:-12])
    with pytest.raises((ValueError, EOFError)):
        ingest.verify_gzip(p)


def test_plan_fingerprint_is_order_independent():
    a = {"snapshots": [{"city": "a", "snapshot_date": "1"}, {"city": "b", "snapshot_date": "2"}]}
    b = {"snapshots": list(reversed(a["snapshots"]))}
    assert ingest.plan_fingerprint(a) == ingest.plan_fingerprint(b)
    assert ingest.plan_fingerprint(a).endswith("-2")


def test_landing_writes_manifest_last_and_is_idempotent(synthetic):
    s, store, plan = synthetic
    manifests = ingest.land_all(s, store, plan)
    assert len(manifests) == 2
    m = store.get_json(ingest.manifest_key(ingest.iab.Snapshot.from_json(plan["snapshots"][0])))
    assert set(m["files"]) == {"listings", "calendar", "reviews"}
    assert all(len(f["sha256"]) == 64 for f in m["files"].values())
    first_landed = m["landed_at"]
    again = ingest.land_all(s, store, plan)  # unchanged -> skipped, same manifest
    assert again[0]["landed_at"] == first_landed
    assert all(r.passed for r in landing_checks(store, plan))


def test_landing_gate_catches_missing_required_column(synthetic):
    s, store, plan = synthetic
    ingest.land_all(s, store, plan)
    snap = ingest.iab.Snapshot.from_json(plan["snapshots"][0])
    path = store.local_path(ingest.file_key(snap, "listings"))
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("id,host_id\n1,2\n")  # room_type, price, ... gone
    failed = {r.name for r in landing_checks(store, plan) if not r.passed}
    assert failed == {"required_columns_present"}


def test_bronze_types_drift_and_rerun(synthetic):
    s, store, plan = synthetic
    ingest.land_all(s, store, plan)
    catalog = get_catalog(s)
    first = bronze.load_all(catalog, store, plan)
    listings = [r for r in first if r.table == "listings"]
    assert [r.rows for r in listings] == [3, 2]
    # schema drift is reported, not fatal
    assert "some_new_column" in listings[0].extra_columns
    assert "bathrooms" in listings[0].missing_columns
    assert listings[0].cast_failures == {"minimum_nights": 1}

    rows = catalog.load_table("bronze.listings").scan().to_arrow().to_pylist()
    by_id = {(r["snapshot_date"].isoformat(), r["listing_id"]): r for r in rows}
    one = by_id[("2026-01-02", 1)]
    assert one["price_raw"] == "$1,234.50"  # parsed in dbt staging, not here
    assert one["host_is_superhost"] is False
    assert one["instant_bookable"] is True
    assert by_id[("2026-01-02", 3)]["minimum_nights"] is None
    assert by_id[("2026-01-02", 3)]["license"] == "STR-123"

    # reviews are cumulative: only the newest snapshot's file is kept
    reviews = catalog.load_table("bronze.reviews").scan().to_arrow()
    assert set(reviews.column("snapshot_date").to_pylist()) == {date(2026, 4, 2)}
    assert "reviewer_name" not in reviews.schema.names  # names are never loaded

    # rerun: unchanged hashes skip; --force replaces partitions instead of duplicating
    assert all(r.skipped for r in bronze.load_all(catalog, store, plan))
    bronze.load_all(catalog, store, plan, force=True)
    assert catalog.load_table("bronze.listings").scan().to_arrow().num_rows == 5
    assert catalog.load_table("bronze.calendar").scan().to_arrow().num_rows == 5 * 5
    audit = catalog.load_table("bronze.load_audit").scan().to_arrow().to_pylist()
    assert len(audit) == 6  # one row per snapshot x table, overwritten not appended
    first_listings = next(
        a
        for a in audit
        if a["table_name"] == "listings"
        and a["snapshot_date"].day == 2
        and a["snapshot_date"].month == 1
    )
    assert json.loads(first_listings["cast_failures"]) == {"minimum_nights": 1}
