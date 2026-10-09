"""Reconcile our marts with figures Inside Airbnb publishes itself.

1. **City dashboards** (insideairbnb.com/<city>/). The headline numbers are computed in the
   browser, so `--capture` renders each page with headless Chrome and parses the visible text
   (listings, room types, nights booked, price, income, licences, single/multi-listing hosts).
   Captures are stored in `docs/verification/published_dashboards.json` so the comparison is
   reproducible later even after Inside Airbnb moves on to a newer snapshot.
2. **Inside Airbnb's own per-listing estimates** (`estimated_occupancy_l365d`,
   `estimated_revenue_l365d` in listings.csv.gz) versus our independently computed model.
3. **Reviews recount**: reviews in the last 12 months counted from reviews.csv.gz versus the
   published `number_of_reviews_ltm`, for each city's latest snapshot.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from airbnb_lakehouse.config import REPO_ROOT, Settings
from airbnb_lakehouse.expose import connect

log = logging.getLogger(__name__)

CAPTURE_PATH = REPO_ROOT / "docs" / "verification" / "published_dashboards.json"
NUM = r"([\d,]+)"
PCT = r"\(\s*[\d.]+\s*%\s*\)"

PATTERNS = {
    "filtered_total": rf"{NUM} out of {NUM} listings",
    "room_types": rf"{NUM} {PCT} entire homes/apartments {NUM} {PCT} private rooms {NUM} {PCT} shared rooms {NUM} {PCT} hotel rooms",
    "activity": rf"{NUM} average nights booked (\S+) {NUM} price/night (\S+) {NUM} average income",
    "licences": rf"{NUM} {PCT} unlicensed {NUM} {PCT} licensed {NUM} {PCT} exempt {NUM} {PCT} pending",
    "str": rf"{NUM} {PCT} short-term rentals {NUM} {PCT} longer-term rentals",
    "hosts": rf"{NUM} {PCT} single listings {NUM} {PCT} multi-listings",
    "data_date": r"Inside Airbnb data: (\w+ \d{1,2},? \d{4})",
}


def _n(s: str) -> int:
    return int(s.replace(",", ""))


def parse_dashboard_text(text: str) -> dict:
    """Extract the headline figures from a rendered dashboard's visible text."""
    t = re.sub(r"\s+", " ", text)
    out: dict = {}
    if m := re.search(PATTERNS["filtered_total"], t):
        out["filtered_listings"], out["total_listings"] = _n(m[1]), _n(m[2])
    if m := re.search(PATTERNS["room_types"], t):
        out.update(
            entire_homes=_n(m[1]),
            private_rooms=_n(m[2]),
            shared_rooms=_n(m[3]),
            hotel_rooms=_n(m[4]),
        )
    if m := re.search(PATTERNS["activity"], t):
        out.update(
            avg_nights_booked=_n(m[1]), avg_price=_n(m[3]), avg_income=_n(m[5]), currency=m[2]
        )
    if m := re.search(PATTERNS["licences"], t):
        out.update(unlicensed=_n(m[1]), licensed=_n(m[2]), exempt=_n(m[3]), pending=_n(m[4]))
    if m := re.search(PATTERNS["str"], t):
        out.update(short_term=_n(m[1]), longer_term=_n(m[2]))
    if m := re.search(PATTERNS["hosts"], t):
        out.update(single_listings=_n(m[1]), multi_listings=_n(m[2]))
    if m := re.search(PATTERNS["data_date"], t):
        out["data_date_text"] = m[1]
        with contextlib.suppress(ValueError):
            out["data_date"] = (
                datetime.strptime(m[1].replace(",", ""), "%B %d %Y").date().isoformat()
            )
    return out


def _chrome() -> str | None:
    for c in (
        os.environ.get("AIRBNB_CHROME"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ):
        if c and Path(c).exists():
            return c
    return None


def _visible_text(html: str) -> str:
    import html as h

    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return h.unescape(re.sub(r"<[^>]+>", " ", s))


def capture_dashboard(city: str, timeout: int = 150) -> dict:
    chrome = _chrome()
    if chrome is None:
        raise RuntimeError("no Chrome/Chromium found (set AIRBNB_CHROME)")
    with tempfile.TemporaryDirectory() as profile:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-sandbox",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=60000",
                "--dump-dom",
                f"https://insideairbnb.com/{city}/",
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    figures = parse_dashboard_text(_visible_text(proc.stdout.decode("utf-8", "replace")))
    figures["url"] = f"https://insideairbnb.com/{city}/"
    figures["captured_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return figures


# --- comparisons -------------------------------------------------------------------------

DASH_FIELDS = [
    # (published key, mart column in scope 'dashboard_default' or a SQL expression)
    ("total_listings", "total_listings"),
    ("filtered_listings", "listings"),
    ("entire_homes", "entire_homes"),
    ("private_rooms", "private_rooms"),
    ("shared_rooms", "shared_rooms"),
    ("hotel_rooms", "hotel_rooms"),
    ("avg_nights_booked", "dash_avg_nights_booked"),
    ("avg_price", "dash_avg_price"),
    ("avg_income", "dash_avg_income"),
    ("unlicensed", "unlicensed"),
    ("licensed", "licensed"),
    ("exempt", "exempt"),
    ("pending", "pending"),
    ("single_listings", "single_host_listings"),
    ("multi_listings", "multi_host_listings"),
]


def records(cur) -> list[dict]:
    """Rows of a DuckDB cursor as dicts (no pandas dependency)."""
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def compare_dashboards(con, captures: dict) -> list[dict]:
    rows = []
    for city, pub in sorted(captures.items()):
        d = pub.get("data_date")
        ours = con.execute(
            """SELECT d.*, a.listings AS total_listings
               FROM marts.mart_city_snapshot_summary d
               JOIN marts.mart_city_snapshot_summary a
                 ON a.city = d.city AND a.snapshot_date = d.snapshot_date AND a.scope = 'all'
               WHERE d.scope = 'dashboard_default' AND d.city = ? AND d.snapshot_date = ?""",
            [city, d],
        )
        ours = records(ours)
        if not ours:
            rows.append(
                {
                    "city": city,
                    "snapshot_date": d,
                    "metric": "(snapshot)",
                    "published": None,
                    "ours": None,
                    "diff": None,
                    "match": False,
                    "note": "the dashboard shows a snapshot this run did not load",
                }
            )
            continue
        rec = ours[0]
        for key, col in DASH_FIELDS:
            if key not in pub:
                continue
            mine = round(float(rec[col])) if rec[col] is not None else None
            diff = None if mine is None else mine - pub[key]
            rows.append(
                {
                    "city": city,
                    "snapshot_date": d,
                    "metric": key,
                    "published": pub[key],
                    "ours": mine,
                    "diff": diff,
                    "match": diff == 0,
                }
            )
    return rows


def compare_published_estimates(con) -> list[dict]:
    return records(
        con.execute(
            """SELECT city, snapshot_date,
              count(*) AS listings,
              avg(CASE WHEN est_nights_ltm = iab_estimated_occupancy_l365d THEN 1.0 ELSE 0 END) AS nights_exact_share,
              avg(CASE WHEN abs(est_nights_ltm - iab_estimated_occupancy_l365d)
                       <= greatest(1, 0.1 * iab_estimated_occupancy_l365d) THEN 1.0 ELSE 0 END) AS nights_within_10pct_share,
              sum(est_nights_ltm) AS our_nights, sum(iab_estimated_occupancy_l365d) AS published_nights,
              sum(CASE WHEN iab_estimated_revenue_l365d IS NOT NULL THEN est_revenue_ltm END) AS our_revenue,
              sum(iab_estimated_revenue_l365d) AS published_revenue
           FROM marts.fct_listing_snapshot
           WHERE iab_estimated_occupancy_l365d IS NOT NULL
           GROUP BY ALL ORDER BY ALL"""
        )
    )


def compare_review_recount(con) -> list[dict]:
    return records(
        con.execute(
            """SELECT city, snapshot_date, count(*) AS listings,
              sum(number_of_reviews_ltm) AS published_reviews_ltm,
              sum(reviews_ltm_recount) AS recounted_reviews_ltm,
              avg(CASE WHEN reviews_ltm_recount = number_of_reviews_ltm THEN 1.0 ELSE 0 END) AS exact_share
           FROM marts.fct_listing_snapshot
           WHERE reviews_file_is_this_snapshot
           GROUP BY ALL ORDER BY ALL"""
        )
    )


def _jsonable(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        clean = {}
        for k, raw in r.items():
            v = raw
            if hasattr(v, "isoformat"):
                v = v.isoformat()[:10]
            elif hasattr(v, "item"):
                v = v.item()
            if isinstance(v, float):
                v = round(v, 4)
            clean[k] = v
        out.append(clean)
    return out


def run_verification(settings: Settings, *, capture: bool = False, out: Path | None = None) -> dict:
    captures = json.loads(CAPTURE_PATH.read_text(encoding="utf-8")) if CAPTURE_PATH.exists() else {}
    live: list[str] = []  # cities re-read from the live dashboard in this run
    if capture:
        con = connect(settings, read_only=True)
        cities = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT city FROM marts.mart_city_snapshot_summary"
            ).fetchall()
        ]
        con.close()
        for city in cities:
            try:
                captures[city] = capture_dashboard(city)
                live.append(city)
                log.info("verify: captured %s (%s)", city, captures[city].get("data_date"))
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                log.warning(
                    "verify: could not capture %s (using the stored capture, if any): %s",
                    city,
                    exc,
                )
        CAPTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CAPTURE_PATH.write_text(
            json.dumps(captures, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    con = connect(settings, read_only=True)
    try:
        dash = compare_dashboards(con, captures) if captures else []
        est = _jsonable(compare_published_estimates(con))
        rec = _jsonable(compare_review_recount(con))
    finally:
        con.close()
    dash = _jsonable(dash)
    result = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dashboards": dash,
        "published_estimates": est,
        "review_recount": rec,
        "summary": {
            "dashboard_metrics_compared": len(dash),
            "dashboard_metrics_exact": sum(1 for r in dash if r["match"]),
            "cities_captured": sorted(captures),
            # A failed live capture falls back to the stored one; say which is which.
            "cities_captured_live": sorted(live),
            "cities_from_stored_capture": sorted(set(captures) - set(live)),
            "captured_at": {c: captures[c].get("captured_at") for c in sorted(captures)},
        },
    }
    target = out or settings.state_dir / "verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
