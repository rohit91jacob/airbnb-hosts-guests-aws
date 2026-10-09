"""Discover snapshots and land the raw files in the (emulated) S3 landing zone.

Landing layout (identical locally and on S3):

    landing/insideairbnb/city=<city>/snapshot_date=<YYYY-MM-DD>/{listings,calendar,reviews}.csv.gz
    landing/insideairbnb/city=<city>/snapshot_date=<YYYY-MM-DD>/_manifest.json
    landing/insideairbnb/_meta/city_params/city=<city>.json

Files are landed byte-for-byte as published. The manifest records URL, ETag, Last-Modified,
size and SHA-256, and is written last, so a snapshot only counts as landed once every file
in it has been verified. Re-running is a no-op for snapshots already landed (Inside Airbnb
snapshot files are immutable per date); `--force` re-downloads and re-verifies.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import zlib
from datetime import UTC, datetime
from pathlib import Path

import requests

from airbnb_lakehouse.config import USER_AGENT, Settings
from airbnb_lakehouse.sources import insideairbnb as iab
from airbnb_lakehouse.storage import ObjectStore, sha256_file

log = logging.getLogger(__name__)

LANDING_PREFIX = "landing/insideairbnb"


def snapshot_prefix(snap: iab.Snapshot) -> str:
    return f"{LANDING_PREFIX}/city={snap.city}/snapshot_date={snap.snapshot_date.isoformat()}"


def file_key(snap: iab.Snapshot, name: str) -> str:
    return f"{snapshot_prefix(snap)}/{name}.csv.gz"


def manifest_key(snap: iab.Snapshot) -> str:
    return f"{snapshot_prefix(snap)}/_manifest.json"


def params_key(city: str) -> str:
    return f"{LANDING_PREFIX}/_meta/city_params/city={city}.json"


# --- plan ----------------------------------------------------------------------------------


PLAN_KEY = "state/plan.json"


def plan_path(settings: Settings) -> Path:
    return settings.state_dir / "plan.json"


def build_plan(settings: Settings) -> dict:
    """Which snapshots to process, plus the dashboard parameters for each city."""
    if settings.source == "fixtures":
        catalog, params = iab.fixture_catalog(settings.fixtures_dir)
        cities = tuple(c for c in settings.cities if c in params) or tuple(sorted(params))
    elif settings.source == "insideairbnb":
        catalog = iab.fetch_catalog(settings.http_timeout)
        cities = settings.cities
        params = {c: iab.fetch_city_params(c, settings.http_timeout) for c in cities}
    else:
        raise ValueError(
            f"AIRBNB_SOURCE must be 'insideairbnb' or 'fixtures', got {settings.source!r}"
        )
    snaps = iab.select_snapshots(catalog, cities, settings.snapshots_per_city)
    plan = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": settings.source,
        "snapshots": [s.to_json() for s in snaps],
        "city_params": {c: params[c].__dict__ for c in cities},
    }
    plan["fingerprint"] = plan_fingerprint(plan)
    return plan


def plan_fingerprint(plan: dict) -> str:
    """Stable identifier of the snapshot set (used as a CI cache key)."""
    ids = sorted(f"{s['city']}@{s['snapshot_date']}" for s in plan["snapshots"])
    return zlib.crc32(",".join(ids).encode()).to_bytes(4, "big").hex() + "-" + str(len(ids))


def write_plan(settings: Settings, plan: dict, store: ObjectStore | None = None) -> Path:
    """Persist the plan locally and (for multi-task runs on AWS) in the object store."""
    path = plan_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    if store is not None:
        store.put_json(PLAN_KEY, plan)
    return path


def read_plan(settings: Settings, store: ObjectStore | None = None) -> dict:
    path = plan_path(settings)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if store is not None and (plan := store.get_json(PLAN_KEY)) is not None:
        return plan
    raise FileNotFoundError(f"{path} not found: run `airbnb discover` first")


# --- download + verify ---------------------------------------------------------------------


def verify_gzip(path: Path) -> int:
    """Fully decompress (streaming) to prove the archive is intact. Returns raw byte count."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    total = 0
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            total += len(d.decompress(chunk))
    total += len(d.flush())
    if not d.eof:
        raise ValueError(f"{path.name}: truncated gzip stream")
    return total


def _download(url: str, dest_dir: Path, timeout: float) -> tuple[Path, dict]:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.mount(
        "https://",
        requests.adapters.HTTPAdapter(
            max_retries=requests.adapters.Retry(
                total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504)
            )
        ),
    )
    with session.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".part")
        with os.fdopen(fd, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        meta = {
            "url": url,
            "etag": r.headers.get("ETag", "").strip('"'),
            "last_modified": r.headers.get("Last-Modified"),
        }
    return Path(tmp), meta


def land_snapshot(
    settings: Settings, store: ObjectStore, snap: iab.Snapshot, *, force: bool = False
) -> dict:
    """Land one snapshot. Returns the manifest (existing one when already landed)."""
    existing = store.get_json(manifest_key(snap))
    if existing and not force and all(store.exists(file_key(snap, n)) for n in iab.FILES):
        log.info("landing: %s %s already landed, skipping", snap.city, snap.snapshot_date)
        return existing
    tmp_dir = settings.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in iab.FILES:
        if settings.source == "fixtures":
            src = iab.fixture_file(settings.fixtures_dir, snap, name)
            meta = {"url": src.as_posix(), "etag": "", "last_modified": None}
            local, cleanup = src, False
        else:
            url = snap.file_url(name)
            log.info("landing: downloading %s", url)
            local, meta = _download(url, tmp_dir, settings.http_timeout)
            cleanup = True
        try:
            raw_bytes = verify_gzip(local)
            files[name] = {
                **meta,
                "bytes": local.stat().st_size,
                "uncompressed_bytes": raw_bytes,
                "sha256": sha256_file(local),
                "key": file_key(snap, name),
            }
            store.put_file(file_key(snap, name), local)
        finally:
            if cleanup:
                local.unlink(missing_ok=True)
    manifest = {
        "snapshot": snap.to_json(),
        "files": files,
        "landed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": settings.source,
    }
    store.put_json(manifest_key(snap), manifest)
    log.info(
        "landing: %s %s landed (%.1f MB)",
        snap.city,
        snap.snapshot_date,
        sum(f["bytes"] for f in files.values()) / 1e6,
    )
    return manifest


def land_all(
    settings: Settings, store: ObjectStore, plan: dict, *, force: bool = False
) -> list[dict]:
    for city, params in plan["city_params"].items():
        store.put_json(
            params_key(city),
            {**params, "captured_at": plan["generated_at"], "source": plan["source"]},
        )
    return [
        land_snapshot(settings, store, iab.Snapshot.from_json(s), force=force)
        for s in plan["snapshots"]
    ]
