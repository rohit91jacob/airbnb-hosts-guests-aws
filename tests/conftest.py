"""Shared test helpers.

Two kinds of input data are used:

* **Real-data fixture sample** (`tests/fixtures/insideairbnb/`, gitignored). Inside Airbnb asks
  people not to republish its data, so the sample is not committed: `make fixtures` (or CI)
  builds it from a live download with `airbnb fixtures`. End-to-end and AWS tests need it and
  skip when it is absent, unless AIRBNB_REQUIRE_FIXTURES=1 (CI), in which case they fail.
* **Synthetic test vectors** (see `make_snapshot_dir` below). These are a few hand-written rows
  in Inside Airbnb's CSV layout, used by unit tests that need exact, known answers. They are
  invented and are labelled as synthetic wherever they are used.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(
    os.environ.get("AIRBNB_FIXTURES_DIR") or REPO_ROOT / "tests" / "fixtures" / "insideairbnb"
)

AIRBNB_ENV = (
    "AIRBNB_DATA_DIR",
    "AIRBNB_SOURCE",
    "AIRBNB_CITIES",
    "AIRBNB_SNAPSHOTS_PER_CITY",
    "AIRBNB_STORAGE",
    "AIRBNB_CATALOG",
    "AIRBNB_DUCKDB_PATH",
    "AIRBNB_LAKE_BUCKET",
    "AWS_ENDPOINT_URL",
)


def fixtures_available() -> bool:
    return (FIXTURES_DIR / "catalog.json").exists()


@pytest.fixture
def real_fixtures() -> Path:
    if not fixtures_available():
        if os.environ.get("AIRBNB_REQUIRE_FIXTURES") == "1":
            pytest.fail(f"fixture sample missing at {FIXTURES_DIR}; run `make fixtures`")
        pytest.skip("real-data fixture sample not built (run `make fixtures`)")
    return FIXTURES_DIR


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Isolate every AIRBNB_* setting; data goes to a temp dir."""
    for k in AIRBNB_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AIRBNB_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


# --- synthetic test vectors -------------------------------------------------------------

LISTING_HEADER = [
    "id", "scrape_id", "last_scraped", "source", "name", "host_id", "host_name", "host_since",
    "host_is_superhost", "host_listings_count", "neighbourhood_cleansed", "latitude",
    "longitude", "property_type", "room_type", "accommodates", "bedrooms", "beds", "price",
    "minimum_nights", "maximum_nights", "availability_365", "number_of_reviews",
    "number_of_reviews_ltm", "estimated_occupancy_l365d", "estimated_revenue_l365d",
    "last_review", "license", "instant_bookable", "calculated_host_listings_count",
    "some_new_column",
]  # fmt: skip


def synthetic_listing(listing_id: int, host_id: int, **over) -> dict:
    """One SYNTHETIC listing row (invented values, Inside Airbnb column layout)."""
    row = {
        "id": str(listing_id), "scrape_id": "1", "last_scraped": "2026-01-02",
        "source": "city scrape", "name": f"Synthetic listing {listing_id}",
        "host_id": str(host_id), "host_name": "Synthetic host", "host_since": "2020-01-01",
        "host_is_superhost": "f", "host_listings_count": "1",
        "neighbourhood_cleansed": "Centrum", "latitude": "52.37", "longitude": "4.89",
        "property_type": "Entire rental unit", "room_type": "Entire home/apt",
        "accommodates": "2", "bedrooms": "1", "beds": "1", "price": "$1,234.50",
        "minimum_nights": "2", "maximum_nights": "30", "availability_365": "100",
        "number_of_reviews": "10", "number_of_reviews_ltm": "4",
        "estimated_occupancy_l365d": "24", "estimated_revenue_l365d": "29628",
        "last_review": "2025-12-20", "license": "", "instant_bookable": "t",
        "calculated_host_listings_count": "1", "some_new_column": "x",
    }  # fmt: skip
    row.update({k: str(v) for k, v in over.items()})
    return row


def write_gz_csv(path: Path, header: list[str], rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({h: r.get(h, "") for h in header})
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    return path


def make_snapshot_dir(root: Path, city: str, day: str, listings: list[dict], nights: int = 365):
    """Write a SYNTHETIC snapshot (listings, calendar, reviews) in fixture-dir layout."""
    d = root / city / day
    write_gz_csv(d / "listings.csv.gz", LISTING_HEADER, listings)
    import datetime as dt

    start = dt.date.fromisoformat(day)
    cal = [
        {
            "listing_id": r["id"],
            "date": (start + dt.timedelta(days=i)).isoformat(),
            "available": "t" if i % 3 else "f",
            "price": "",
            "adjusted_price": "",
            "minimum_nights": "2",
            "maximum_nights": "30",
        }
        for r in listings
        for i in range(nights)
    ]
    write_gz_csv(
        d / "calendar.csv.gz",
        [
            "listing_id",
            "date",
            "available",
            "price",
            "adjusted_price",
            "minimum_nights",
            "maximum_nights",
        ],
        cal,
    )
    rev = [
        {
            "listing_id": r["id"],
            "id": str(int(r["id"]) * 100 + j),
            "date": "2025-11-0" + str(j + 1),
            "reviewer_id": str(900 + j),
            "reviewer_name": "Synthetic reviewer",
            "comments": "Great clean place, lovely host" if j % 2 == 0 else "Dirty and noisy",
        }
        for r in listings
        for j in range(2)
    ]
    write_gz_csv(
        d / "reviews.csv.gz",
        ["listing_id", "id", "date", "reviewer_id", "reviewer_name", "comments"],
        rev,
    )
    return d


def write_synthetic_catalog(root: Path, snapshots: list[tuple[str, str]]) -> Path:
    """catalog.json for a SYNTHETIC fixture dir (same shape `airbnb fixtures` writes)."""
    meta = {
        "note": "SYNTHETIC unit-test vectors (invented rows), not Inside Airbnb data.",
        "snapshots": [
            {
                "city": c,
                "city_name": c.title(),
                "country": "Testland",
                "region": "",
                "snapshot_date": d,
                "scrape_id": "1",
                "data_root": f"https://data.insideairbnb.com/testland/{c}/",
            }
            for c, d in snapshots
        ],
        "city_params": {
            c: {
                "city": c,
                "average_nights_stay": 3.0,
                "night_per_year_threshold": 60,
                "str_threshold": None,
                "requires_license": False,
                "currency_symbol": "$",
                "visualisation_version": None,
                "dashboard_data_date": None,
            }
            for c in {c for c, _ in snapshots}
        },
    }
    (root / "catalog.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return root
