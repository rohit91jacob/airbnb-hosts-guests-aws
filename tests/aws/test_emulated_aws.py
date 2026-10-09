"""AWS code paths on an EMULATOR (moto in-process by default; LocalStack in CI).

Set AIRBNB_AWS_TEST_ENDPOINT (e.g. http://localhost:4566) to target a running emulator, and
AIRBNB_AWS_TEST_CATALOG=sql when that emulator has no Glue (LocalStack community).
"""

from __future__ import annotations

import os

import duckdb
import pytest

from airbnb_lakehouse.aws.smoke import ATHENA_BYTES_CUTOFF, run_smoke

pytestmark = pytest.mark.aws


def _expected_rows(fixtures) -> dict[str, int]:
    con = duckdb.connect()
    n = lambda glob: con.execute(  # noqa: E731
        f"SELECT count(*) FROM read_csv('{(fixtures / glob).as_posix()}', header=true, all_varchar=true, max_line_size=20000000)"
    ).fetchone()[0]
    # reviews: bronze keeps only each city's newest (cumulative) file
    newest = {}
    for p in fixtures.glob("*/*/reviews.csv.gz"):
        newest[p.parent.parent.name] = max(newest.get(p.parent.parent.name, p), p)
    reviews = sum(n(p.relative_to(fixtures).as_posix()) for p in newest.values())
    return {
        "listings": n("*/*/listings.csv.gz"),
        "calendar": n("*/*/calendar.csv.gz"),
        "reviews": reviews,
    }


def test_pipeline_on_emulated_aws(real_fixtures, clean_env):
    catalog = os.environ.get("AIRBNB_AWS_TEST_CATALOG", "glue")
    res = run_smoke(
        os.environ.get("AIRBNB_AWS_TEST_ENDPOINT") or None,
        fixtures_dir=real_fixtures,
        workdir=clean_env / "smoke",
        catalog=catalog,
    )
    expected = _expected_rows(real_fixtures)
    assert res["landing_errors"] == [] and res["bronze_dq_errors"] == []
    assert res["landed_objects"] == 4 * len(res["snapshots"]) + len(
        {s.split("@")[0] for s in res["snapshots"]}
    )
    assert {k: res["iceberg_rows"][k] for k in expected} == expected
    assert res["duckdb_rows_via_s3"] == expected  # DuckDB read the Iceberg tables back from S3
    assert res["iceberg_rows"]["snapshots"] == len(res["snapshots"])
    assert res["rerun_loads"] == 0
    assert set(res["registered_tables"]) >= {
        "listings",
        "calendar",
        "reviews",
        "snapshots",
        "load_audit",
    }
    assert all(loc.startswith("s3://") for loc in res["metadata_locations"].values())
    assert res["state_machine_states"][:3] == ["Discover", "Land", "LandingDQ"]
    assert res["schedule_target"] == res["state_machine_arn"]
    if catalog == "glue":
        assert res["athena_bytes_scanned_cutoff"] == ATHENA_BYTES_CUTOFF
