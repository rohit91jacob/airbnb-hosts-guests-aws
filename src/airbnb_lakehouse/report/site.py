"""`airbnb report`: a static results site (index.html + summary.json) built from the marts.

Every number on the page is read from the DuckDB warehouse produced by the last run; nothing
is typed in by hand. The site is self-contained (inline CSS, inline SVG, a few lines of JS for
chart tooltips) so it can be published to GitHub Pages or an S3 static website as-is.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from airbnb_lakehouse.config import Settings
from airbnb_lakehouse.expose import connect
from airbnb_lakehouse.report.charts import line_chart
from airbnb_lakehouse.verify import records

log = logging.getLogger(__name__)

# Categorical slots 1-3 of the reference palette (validated all-pairs, light and dark).
CITY_SLOTS = ["--series-1", "--series-2", "--series-3", "--series-4"]


def _q(con, sql: str, params=None) -> list[dict]:
    return records(con.execute(sql, params or []))


DASH = "\N{EN DASH}"  # shown for missing values


def _fmt_int(v) -> str:
    return DASH if v is None else f"{round(v):,}"


def _fmt_pct(v, digits: int = 1) -> str:
    return DASH if v is None else f"{v * 100:.{digits}f}%"


def _fmt_money(v, sym: str = "") -> str:
    return DASH if v is None else f"{sym}{round(v):,}"


def _fmt_signed_pct(v) -> str:
    return DASH if v is None else f"{v * 100:+.1f}%"


def collect(con) -> dict:
    snaps = _q(
        con,
        """SELECT city, city_name, country, snapshot_date, currency_symbol, average_nights_stay,
                              str_threshold_nights, is_latest_snapshot, previous_snapshot_date
                       FROM marts.dim_city_snapshot ORDER BY city, snapshot_date""",
    )
    latest = {s["city"]: s for s in snaps if s["is_latest_snapshot"]}
    cities = sorted(latest)
    summary = {
        (r["city"], r["snapshot_date"], r["scope"]): r
        for r in _q(con, "SELECT * FROM marts.mart_city_snapshot_summary")
    }
    hosts = {
        (r["city"], r["snapshot_date"]): r
        for r in _q(con, "SELECT * FROM marts.mart_host_portfolio")
    }
    occ = {
        (r["city"], r["snapshot_date"]): r
        for r in _q(con, "SELECT * FROM marts.mart_occupancy_availability WHERE room_type = 'All'")
    }
    rev = {
        (r["city"], r["snapshot_date"], r["room_type"]): r
        for r in _q(con, "SELECT * FROM marts.mart_revenue_estimates")
    }
    price = {
        (r["city"], r["snapshot_date"]): r
        for r in _q(con, "SELECT * FROM marts.mart_price_change_summary")
    }
    velocity = _q(
        con,
        """SELECT city, review_month, reviews, reviews_yoy_change, is_partial_month
                          FROM marts.mart_review_velocity ORDER BY city, review_month""",
    )
    sentiment = _q(
        con,
        """SELECT city, sum(reviews) AS reviews, sum(reviews_with_signal) AS with_signal,
                                  sum(reviews_with_signal) * 1.0 / sum(reviews) AS coverage,
                                  sum(positive_share * reviews_with_signal) / sum(reviews_with_signal) AS positive_share,
                                  sum(negative_share * reviews_with_signal) / sum(reviews_with_signal) AS negative_share
                           FROM marts.mart_guest_sentiment
                           WHERE review_month >= (SELECT max(review_month) FROM marts.mart_guest_sentiment) - INTERVAL 12 MONTH
                           GROUP BY city ORDER BY city""",
    )
    neighbourhoods = _q(
        con,
        """SELECT n.* FROM marts.mart_neighbourhood_supply n
                                JOIN marts.dim_city_snapshot s ON s.city = n.city AND s.snapshot_date = n.snapshot_date
                                WHERE s.is_latest_snapshot
                                QUALIFY row_number() OVER (PARTITION BY n.city ORDER BY n.listings DESC) <= 8
                                ORDER BY n.city, n.listings DESC""",
    )
    compliance = _q(
        con,
        """SELECT c.* FROM marts.mart_licence_compliance c
                            JOIN marts.dim_city_snapshot s ON s.city = c.city AND s.snapshot_date = c.snapshot_date
                            WHERE s.is_latest_snapshot ORDER BY c.city, c.rule_id""",
    )
    top_hosts = _q(
        con,
        """SELECT t.* FROM marts.mart_top_hosts t
                           JOIN marts.dim_city_snapshot s ON s.city = t.city AND s.snapshot_date = t.snapshot_date
                           WHERE s.is_latest_snapshot AND t.host_rank <= 5 ORDER BY t.city, t.host_rank""",
    )
    scd2 = _q(
        con,
        """SELECT count(*) AS versions, count(DISTINCT listing_key) AS listings,
                             sum(CASE WHEN dbt_valid_to IS NULL THEN 1 ELSE 0 END) AS current_versions
                      FROM snapshots.snap_listing_attributes""",
    )[0]
    return dict(  # noqa: C408 - keyword form reads better for 14 fields
        snaps=snaps,
        latest=latest,
        cities=cities,
        summary=summary,
        hosts=hosts,
        occ=occ,
        rev=rev,
        price=price,
        velocity=velocity,
        sentiment=sentiment,
        neighbourhoods=neighbourhoods,
        compliance=compliance,
        top_hosts=top_hosts,
        scd2=scd2,
    )


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def build_site(settings: Settings, out: Path) -> Path:
    con = connect(settings, read_only=True)
    try:
        d = collect(con)
    finally:
        con.close()
    out.mkdir(parents=True, exist_ok=True)

    city_rows = []
    for city in d["cities"]:
        last = d["latest"][city]
        dt, prev_dt = last["snapshot_date"], last["previous_snapshot_date"]
        a = d["summary"][(city, dt, "all")]
        pa = d["summary"].get((city, prev_dt, "all")) if prev_dt else None
        h = d["hosts"][(city, dt)]
        o = d["occ"][(city, dt)]
        r = d["rev"][(city, dt, "All")]
        p = d["price"].get((city, dt))
        city_rows.append(
            {
                "city": city,
                "name": last["city_name"],
                "country": last["country"],
                "date": dt,
                "prev_date": prev_dt,
                "sym": last["currency_symbol"] or "",
                "avg_stay": last["average_nights_stay"],
                "listings": a["listings"],
                "listings_prev": pa["listings"] if pa else None,
                "listings_change": (a["listings"] - pa["listings"]) / pa["listings"]
                if pa
                else None,
                "hosts": h["hosts"],
                "entire_share": a["entire_home_share"],
                "short_term_share": a["short_term_share"],
                "median_price": a["median_price"],
                "single_host_share": h["single_listing_host_share"],
                "multi_host_listing_share": h["multi_listing_host_listing_share"],
                "ten_plus_share": h["ten_plus_host_listing_share"],
                "superhost_hosts": h["superhost_share_of_hosts"],
                "superhost_listings": h["superhost_share_of_listings"],
                "new_hosts": h["new_hosts"],
                "departed_hosts": h["departed_hosts"],
                "occ": o["mean_est_occupancy_rate"],
                "unavail30": o["mean_unavailable_share_30"],
                "highly_available": o["highly_available_share"],
                "frequently_booked": o["frequently_booked_share"],
                "adr": r["est_adr"],
                "revenue": r["est_revenue_ltm"],
                "median_rev": r["median_est_revenue_per_priced_listing"],
                "revpal": r["est_revpal"],
                "priced_share": a["priced_share"],
                "unlicensed_share": a["unlicensed_share"],
                "price": p,
            }
        )

    # Review velocity chart: last 24 complete months per city.
    series = []
    for i, city in enumerate(d["cities"]):
        pts = [
            (v["review_month"], v["reviews"])
            for v in d["velocity"]
            if v["city"] == city and not v["is_partial_month"]
        ][-24:]
        series.append(
            {"name": d["latest"][city]["city_name"], "slot": CITY_SLOTS[i % 4], "points": pts}
        )
    velocity_svg = line_chart(series, y_label="reviews per month", width=760, height=280)
    velocity_table = [
        {
            "month": v["review_month"],
            "city": d["latest"][v["city"]]["city_name"],
            "reviews": v["reviews"],
            "yoy": v["reviews_yoy_change"],
        }
        for v in d["velocity"]
        if not v["is_partial_month"]
    ][-72:]

    dq = {
        layer: _read_json(settings.state_dir / f"dq_{layer}.json")
        for layer in ("landing", "bronze", "marts")
    }
    verification = _read_json(settings.state_dir / "verification.json")
    plan = _read_json(settings.state_dir / "plan.json") or {}

    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "Inside Airbnb (CC BY 4.0) - https://insideairbnb.com/get-the-data/",
        "plan_fingerprint": plan.get("fingerprint"),
        "snapshots": [f"{s['city']}@{s['snapshot_date']}" for s in d["snaps"]],
        "scd2": d["scd2"],
        "cities": {
            c["city"]: {
                k: c[k]
                for k in (
                    "date",
                    "listings",
                    "hosts",
                    "entire_share",
                    "multi_host_listing_share",
                    "superhost_hosts",
                    "occ",
                    "adr",
                    "unlicensed_share",
                )
            }
            for c in city_rows
        },
        "dq": {k: (v or {}).get("errors_failed") for k, v in dq.items()},
        "verification": (verification or {}).get("summary"),
    }

    env = Environment(
        loader=PackageLoader("airbnb_lakehouse.report", "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters.update(int=_fmt_int, pct=_fmt_pct, money=_fmt_money, spct=_fmt_signed_pct)
    html = env.get_template("index.html.j2").render(
        d=d,
        cities=city_rows,
        velocity_svg=velocity_svg,
        velocity_table=velocity_table,
        dq=dq,
        verification=verification,
        summary=summary,
        repo_url=os.environ.get(
            "AIRBNB_REPO_URL", "https://github.com/rohit91jacob/airbnb-hosts-guests-aws"
        ),
        today=date.today().isoformat(),
    )
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out / "index.html"


CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".json": "application/json"}


def publish_site(settings: Settings, out: Path, bucket: str, prefix: str = "site/") -> list[str]:
    """Upload the built site to S3 (the Report task on AWS; moto/LocalStack in tests)."""
    import boto3

    s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, region_name=settings.aws_region)
    keys = []
    for path in sorted(p for p in out.rglob("*") if p.is_file()):
        key = prefix + path.relative_to(out).as_posix()
        extra = {"ContentType": CONTENT_TYPES.get(path.suffix, "application/octet-stream")}
        s3.upload_file(str(path), bucket, key, ExtraArgs=extra)
        keys.append(key)
    log.info("report: published %d files to s3://%s/%s", len(keys), bucket, prefix)
    return keys
