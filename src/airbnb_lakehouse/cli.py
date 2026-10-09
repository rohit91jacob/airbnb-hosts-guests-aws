"""`airbnb` command line.

Exit codes: 0 success, 3 data-quality gate failed (error-level check), 1 any other failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from airbnb_lakehouse.config import load_settings

log = logging.getLogger("airbnb_lakehouse")


def _setup_logging() -> None:
    if logging.getLogger().handlers:
        return
    if os.environ.get("AIRBNB_LOG_FORMAT") == "json":
        fmt = '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    else:
        fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    logging.basicConfig(level=os.environ.get("AIRBNB_LOG_LEVEL", "INFO"), format=fmt)
    for noisy in ("botocore", "urllib3", "pyiceberg", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --- commands ----------------------------------------------------------------------------


def cmd_discover(args) -> int:
    from airbnb_lakehouse import ingest
    from airbnb_lakehouse.storage import get_store

    s = load_settings()
    plan = ingest.build_plan(s)
    ingest.write_plan(s, plan, get_store(s))
    if args.fingerprint:
        print(plan["fingerprint"])
        return 0
    print(
        json.dumps(
            {
                "fingerprint": plan["fingerprint"],
                "snapshots": [f"{x['city']}@{x['snapshot_date']}" for x in plan["snapshots"]],
            },
            indent=2,
        )
    )
    return 0


def cmd_ingest(args) -> int:
    from airbnb_lakehouse import ingest
    from airbnb_lakehouse.storage import get_store

    s = load_settings()
    store = get_store(s)
    ingest.land_all(s, store, ingest.read_plan(s, store), force=args.force)
    return 0


def cmd_bronze(args) -> int:
    from airbnb_lakehouse import bronze, ingest
    from airbnb_lakehouse.catalog import get_catalog
    from airbnb_lakehouse.storage import get_store

    s = load_settings()
    store = get_store(s)
    results = bronze.load_all(get_catalog(s), store, ingest.read_plan(s, store), force=args.force)
    loaded = [r for r in results if not r.skipped]
    log.info(
        "bronze: %d table loads, %d skipped (unchanged)", len(loaded), len(results) - len(loaded)
    )
    return 0


def cmd_expose(_args) -> int:
    from airbnb_lakehouse.catalog import get_catalog
    from airbnb_lakehouse.expose import expose_bronze

    s = load_settings()
    expose_bronze(s, get_catalog(s))
    return 0


def cmd_dq(args) -> int:
    from airbnb_lakehouse import ingest
    from airbnb_lakehouse.expose import connect, prepare_for_checks
    from airbnb_lakehouse.quality.checks import DQ_EXIT_CODE, run_layer
    from airbnb_lakehouse.storage import get_store

    s = load_settings()
    store = get_store(s)
    plan = ingest.read_plan(s, store) if args.layer == "landing" else None
    if args.layer != "landing":
        prepare_for_checks(s, args.layer)
    summary = run_layer(s, args.layer, lambda: connect(s), store=store, plan=plan)
    store.put_json(f"state/dq_{args.layer}.json", summary)
    print(json.dumps({k: summary[k] for k in ("layer", "errors_failed", "warnings_failed")}))
    return DQ_EXIT_CODE if summary["errors_failed"] else 0


def cmd_transform(args) -> int:
    from airbnb_lakehouse.catalog import get_catalog
    from airbnb_lakehouse.expose import expose_bronze
    from airbnb_lakehouse.transform import run_transform

    s = load_settings()
    if args.target != "athena":
        expose_bronze(s, get_catalog(s))
    print(json.dumps(run_transform(s, full_refresh=args.full_refresh, target=args.target)))
    return 0


def cmd_run(args) -> int:
    from airbnb_lakehouse.orchestrate import load_definition, run_definition

    target = args.target

    def rewrite(cmd: list[str]) -> list[str]:
        if cmd[1:2] == ["transform"]:
            cmd = [c if c != "athena" else target for c in cmd]
            if args.full_refresh:
                cmd.append("--full-refresh")
        if cmd[1:2] == ["report"] and args.site:
            cmd += ["--out", args.site]
        return cmd

    ex = run_definition(load_definition(), main, rewrite=rewrite)
    for st in ex.steps:
        log.info("  %-12s exit=%d  %6.1fs", st.state, st.exit_code, st.seconds)
    log.info("execution %s%s", ex.status, f" at {ex.failed_state}" if ex.failed_state else "")
    return ex.exit_code


def cmd_report(args) -> int:
    from airbnb_lakehouse.report.site import build_site, publish_site

    s = load_settings()
    if s.catalog == "glue":  # on AWS the marts live in Glue/S3; map them into DuckDB first
        from airbnb_lakehouse.expose import prepare_for_checks

        prepare_for_checks(s, "marts")
    out = build_site(s, Path(args.out))
    log.info("report: wrote %s", out)
    if bucket := os.environ.get("AIRBNB_SITE_BUCKET"):
        publish_site(s, out.parent, bucket)
    return 0


def cmd_verify(args) -> int:
    from airbnb_lakehouse.verify import run_verification

    s = load_settings()
    res = run_verification(s, capture=args.capture, out=Path(args.out) if args.out else None)
    print(json.dumps(res["summary"], indent=2))
    return 0


def cmd_fixtures(args) -> int:
    from airbnb_lakehouse.fixtures import build_fixtures

    s = load_settings()
    out = Path(args.out) if args.out else s.fixtures_dir
    build_fixtures(s, out, cities=tuple(args.cities.split(",")), listings_per_city=args.listings)
    return 0


def cmd_sql(args) -> int:
    from airbnb_lakehouse.expose import connect

    con = connect(load_settings(), read_only=True)
    try:
        cur = con.execute(args.query)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        con.close()
    print("\t".join(cols))
    for r in rows:
        print("\t".join("" if v is None else str(v) for v in r))
    return 0


def cmd_aws_smoke(args) -> int:
    from airbnb_lakehouse.aws.smoke import run_smoke

    res = run_smoke(endpoint_url=args.endpoint, fixtures_only=True, catalog=args.catalog)
    print(json.dumps(res, indent=2, default=str))
    ok = not res["landing_errors"] and not res["bronze_dq_errors"] and res["rerun_loads"] == 0
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="airbnb", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="list Inside Airbnb snapshots and write the run plan")
    d.add_argument("--fingerprint", action="store_true", help="print only the plan fingerprint")
    d.set_defaults(func=cmd_discover)

    i = sub.add_parser("ingest", help="land planned snapshots in the S3 landing zone")
    i.add_argument("--force", action="store_true", help="re-download already landed snapshots")
    i.set_defaults(func=cmd_ingest)

    b = sub.add_parser("bronze", help="load landed files into Iceberg bronze tables")
    b.add_argument(
        "--force", action="store_true", help="reload even if the source hash is unchanged"
    )
    b.set_defaults(func=cmd_bronze)

    sub.add_parser(
        "expose", help="(re)create DuckDB views over the bronze Iceberg tables"
    ).set_defaults(func=cmd_expose)

    q = sub.add_parser("dq", help="run the data-quality gate for one layer (exit 3 on error)")
    q.add_argument("--layer", choices=["landing", "bronze", "marts"], required=True)
    q.set_defaults(func=cmd_dq)

    t = sub.add_parser("transform", help="dbt seeds, SCD2 replay rounds and build")
    t.add_argument(
        "--target", default=os.environ.get("DBT_TARGET", "dev"), choices=["dev", "ci", "athena"]
    )
    t.add_argument(
        "--full-refresh", action="store_true", help="rebuild the SCD2 history from scratch"
    )
    t.set_defaults(func=cmd_transform)

    r = sub.add_parser(
        "run", help="run the whole pipeline by walking the Step Functions definition"
    )
    r.add_argument("--target", default=os.environ.get("DBT_TARGET", "dev"), choices=["dev", "ci"])
    r.add_argument("--full-refresh", action="store_true")
    r.add_argument("--site", default=None, help="output directory for the results site")
    r.set_defaults(func=cmd_run)

    rp = sub.add_parser("report", help="build the static results site")
    rp.add_argument("--out", default=os.environ.get("AIRBNB_SITE_DIR", "site"))
    rp.set_defaults(func=cmd_report)

    v = sub.add_parser("verify", help="reconcile marts with Inside Airbnb's published figures")
    v.add_argument(
        "--capture",
        action="store_true",
        help="render the live city dashboards with headless Chrome and capture their figures",
    )
    v.add_argument("--out", default=None, help="write the reconciliation JSON here")
    v.set_defaults(func=cmd_verify)

    f = sub.add_parser("fixtures", help="build small real-data test fixtures from a live download")
    f.add_argument("--cities", default="singapore,amsterdam")
    f.add_argument("--listings", type=int, default=120, help="listings sampled per city")
    f.add_argument("--out", default=None)
    f.set_defaults(func=cmd_fixtures)

    sq = sub.add_parser("sql", help="run a SQL query against the DuckDB warehouse")
    sq.add_argument("query")
    sq.set_defaults(func=cmd_sql)

    a = sub.add_parser("aws-smoke", help="exercise the AWS code paths against moto or LocalStack")
    a.add_argument(
        "--endpoint",
        default=os.environ.get("AWS_ENDPOINT_URL"),
        help="emulator endpoint (default: start moto in-process)",
    )
    a.add_argument(
        "--catalog",
        choices=["glue", "sql"],
        default="glue",
        help="sql = SQLite catalog with the Iceberg warehouse on S3 (LocalStack community)",
    )
    a.set_defaults(func=cmd_aws_smoke)
    return p


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        log.error("%s failed: %s", args.command, exc, exc_info=log.isEnabledFor(logging.DEBUG))  # noqa: TRY400 - traceback only at DEBUG
        return 1


if __name__ == "__main__":
    sys.exit(main())
