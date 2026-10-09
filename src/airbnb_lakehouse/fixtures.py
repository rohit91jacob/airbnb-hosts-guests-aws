"""Build small REAL test fixtures from a live Inside Airbnb download.

Inside Airbnb data is CC BY 4.0, but the project asks people not to republish it, so this
repository commits no Inside Airbnb rows. Instead, tests and CI build their fixtures from a
download: the latest N snapshots of a few cities, cut down to a deterministic sample of
listings (lowest md5 of the listing id across the union of snapshots, so the sample contains
listings that persist, appear and disappear between snapshots). Calendar and review rows are
kept only for sampled listings. The files keep Inside Airbnb's exact CSV format.

Output layout (gitignored):  <out>/catalog.json, <out>/<city>/<snapshot_date>/*.csv.gz
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

import duckdb

from airbnb_lakehouse import ingest
from airbnb_lakehouse.config import Settings
from airbnb_lakehouse.sources.insideairbnb import FILES, Snapshot
from airbnb_lakehouse.storage import get_store

log = logging.getLogger(__name__)


def build_fixtures(
    settings: Settings, out: Path, *, cities: tuple[str, ...], listings_per_city: int = 120
) -> Path:
    src = dataclasses.replace(
        settings,
        source="insideairbnb",
        cities=cities,
        storage="local",
        # Shares the landing zone: snapshots already downloaded are not fetched again.
    )
    plan = ingest.build_plan(src)
    store = get_store(src)
    ingest.land_all(src, store, plan)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    snaps = [Snapshot.from_json(s) for s in plan["snapshots"]]
    for city in cities:
        city_snaps = [s for s in snaps if s.city == city]
        paths = [store.local_path(ingest.file_key(s, "listings")).as_posix() for s in city_snaps]
        ids = con.execute(
            f"""SELECT id FROM (SELECT DISTINCT id FROM read_csv({paths!r}, header=true,
                all_varchar=true, hive_partitioning=false, union_by_name=true))
                ORDER BY md5(id) LIMIT {int(listings_per_city)}"""
        ).fetchall()
        con.execute(
            "CREATE OR REPLACE TABLE sample_ids AS SELECT unnest(?) AS id", [[r[0] for r in ids]]
        )
        for snap in city_snaps:
            dest = out / city / snap.snapshot_date.isoformat()
            dest.mkdir(parents=True, exist_ok=True)
            for name in FILES:
                key_col = "id" if name == "listings" else "listing_id"
                path = store.local_path(ingest.file_key(snap, name)).as_posix()
                target = (dest / f"{name}.csv.gz").as_posix()
                con.execute(
                    f"""COPY (SELECT f.* FROM read_csv('{path}', header=true, all_varchar=true,
                          quote='"', escape='"', hive_partitioning=false, max_line_size=20000000) f
                        WHERE f.{key_col} IN (SELECT id FROM sample_ids))
                        TO '{target}' (HEADER, QUOTE '"', COMPRESSION gzip)"""
                )
            log.info("fixtures: %s %s written", city, snap.snapshot_date)
    meta = {
        "note": "Real Inside Airbnb data (CC BY 4.0), sampled by airbnb fixtures. Do not commit.",
        "snapshots": [s.to_json() for s in snaps],
        "city_params": plan["city_params"],
        "listings_per_city": listings_per_city,
    }
    (out / "catalog.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out
