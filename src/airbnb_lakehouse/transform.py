"""Run dbt: seeds, staging, SCD2 replay rounds, then the full build with tests.

SCD2 replay
-----------
Each distinct snapshot date in bronze is a "round". Rounds are applied oldest first; in round
D the snapshot source holds every city's latest snapshot on or before D, and dbt's clock is
pinned to D (see dbt/macros/snapshot_time.sql). Applied rounds are recorded in
`ops.scd2_rounds` so a rerun only applies new rounds (and an unchanged rerun applies none).

If a round *older* than the last applied one appears (e.g. an extra historical snapshot was
added to the plan), history after it would be wrong, so the snapshot table is rebuilt from
scratch by replaying every round. `--full-refresh` forces the same.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from airbnb_lakehouse.config import Settings
from airbnb_lakehouse.expose import connect

log = logging.getLogger(__name__)


class DbtError(RuntimeError):
    pass


def _env_for(settings: Settings, target: str) -> None:
    os.environ["DBT_TARGET"] = target
    os.environ.setdefault("AIRBNB_DUCKDB_PATH", settings.duckdb_path.as_posix())
    os.environ["AIRBNB_DUCKDB_PATH"] = settings.duckdb_path.as_posix()
    os.environ.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")


def dbt(settings: Settings, args: list[str], *, target: str = "dev", vars_: dict | None = None):
    """Invoke dbt in-process (one parse per call, partial parsing on)."""
    from dbt.cli.main import dbtRunner

    _env_for(settings, target)
    dbt_dir: Path = settings.dbt_dir
    cli = [
        *args,
        "--project-dir",
        str(dbt_dir),
        "--profiles-dir",
        str(dbt_dir),
        "--target",
        target,
    ]
    if vars_:
        cli += ["--vars", json.dumps(vars_)]
    log.info("dbt %s", " ".join(cli))
    res = dbtRunner().invoke(cli)
    if not res.success:
        raise DbtError(f"dbt {' '.join(args)} failed: {res.exception or 'see log above'}")
    return res


def snapshot_rounds(settings: Settings) -> list[date]:
    con = connect(settings, read_only=False)
    try:
        rows = con.execute(
            "SELECT DISTINCT snapshot_date FROM bronze.snapshots ORDER BY 1"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def _applied_rounds(settings: Settings) -> list[date]:
    con = connect(settings)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS ops")
        con.execute(
            "CREATE TABLE IF NOT EXISTS ops.scd2_rounds (round_date DATE PRIMARY KEY, applied_at TIMESTAMP)"
        )
        return [
            r[0]
            for r in con.execute("SELECT round_date FROM ops.scd2_rounds ORDER BY 1").fetchall()
        ]
    finally:
        con.close()


def _record_round(settings: Settings, d: date) -> None:
    con = connect(settings)
    try:
        con.execute("INSERT OR REPLACE INTO ops.scd2_rounds VALUES (?, now())", [d])
    finally:
        con.close()


def _reset_snapshot(settings: Settings) -> None:
    con = connect(settings)
    try:
        con.execute("DROP TABLE IF EXISTS snapshots.snap_listing_attributes")
        con.execute("DELETE FROM ops.scd2_rounds")
    finally:
        con.close()


def run_transform(settings: Settings, *, full_refresh: bool = False, target: str = "dev") -> dict:
    rounds = snapshot_rounds(settings)
    if not rounds:
        raise DbtError("bronze.snapshots is empty: nothing to transform")
    applied = _applied_rounds(settings)
    if full_refresh or (applied and min(rounds) < max(applied) and set(rounds) - set(applied)):
        if not full_refresh:
            log.warning(
                "transform: an older snapshot appeared; rebuilding SCD2 history from scratch"
            )
        _reset_snapshot(settings)
        applied = []
    pending = [d for d in rounds if not applied or d > max(applied)]
    last = rounds[-1].isoformat()

    dbt(settings, ["seed", "--full-refresh"], target=target)
    dbt(settings, ["run", "--select", "+int_listing_snapshots"], target=target)
    for d in pending:
        log.info("transform: SCD2 round %s", d)
        dbt(settings, ["snapshot"], target=target, vars_={"as_of_round": d.isoformat()})
        _record_round(settings, d)
    # Snapshot is a no-op here (the last round is already applied); tests run in DAG order.
    dbt(settings, ["build"], target=target, vars_={"as_of_round": last})
    return {"rounds": [d.isoformat() for d in rounds], "applied": [d.isoformat() for d in pending]}
