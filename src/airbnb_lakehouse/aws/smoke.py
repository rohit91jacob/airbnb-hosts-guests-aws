"""Run the AWS-shaped half of the pipeline against an EMULATOR, never a real account.

What it does, in order:

1. Starts moto as an in-process HTTP server (or uses `endpoint_url`, e.g. LocalStack in CI)
   with dummy credentials. It refuses to run with no endpoint, so it cannot reach real AWS.
2. Creates the lake bucket, then lands the fixture snapshots in S3 through `S3ObjectStore`
   (the same code path `airbnb ingest` uses on AWS).
3. Runs the landing data-quality gate against S3.
4. Loads bronze as Apache Iceberg tables registered in the Glue Data Catalog
   (`pyiceberg` GlueCatalog, data and metadata on S3).
5. Exposes those tables to DuckDB with `iceberg_scan('s3://...')` and runs the bronze gate,
   which is what the DQ task does on AWS.
6. Registers the orchestration: the Step Functions definition Terraform deploys, an EventBridge
   Scheduler schedule targeting it, the SNS alert topic and the Athena workgroup with its
   bytes-scanned cutoff. These are control-plane calls; moto does not execute ECS tasks.

The result dict records row counts read back from the Glue-registered tables, so the caller can
check them against the local (filesystem + SQLite catalog) run.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import socket
import tempfile
from contextlib import contextmanager
from pathlib import Path

from airbnb_lakehouse.config import REPO_ROOT, load_settings

log = logging.getLogger(__name__)

ASL_PATH = REPO_ROOT / "infra" / "statemachine" / "pipeline.asl.json"
DUMMY_ACCOUNT = "123456789012"  # moto's default account id; not a real account
ATHENA_BYTES_CUTOFF = 10 * 1024**3


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def emulator(endpoint_url: str | None):
    """Yield an endpoint URL: the given one, or a moto server started in-process."""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    region = os.environ.get("AIRBNB_SMOKE_REGION", "eu-west-1")
    os.environ.update(
        {
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",  # emulator dummy, not a secret
            "AWS_SESSION_TOKEN": "testing",
            "AWS_DEFAULT_REGION": region,
            "AWS_REGION": region,
        }
    )
    server = None
    try:
        if endpoint_url is None:
            from moto.server import ThreadedMotoServer

            logging.getLogger("werkzeug").setLevel(logging.WARNING)

            port = _free_port()
            server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
            server.start()
            endpoint_url = f"http://127.0.0.1:{port}"
            log.info("aws-smoke: moto server on %s", endpoint_url)
        os.environ["AWS_ENDPOINT_URL"] = endpoint_url
        yield endpoint_url
    finally:
        if server is not None:
            server.stop()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_ENV_KEYS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
    "AWS_ENDPOINT_URL",
    "AIRBNB_DUCKDB_PATH",
)


def render_definition(substitutions: dict[str, str]) -> str:
    """Fill the Terraform templatefile placeholders in the ASL file (JSON-encoded values)."""
    text = ASL_PATH.read_text(encoding="utf-8")
    for key, value in substitutions.items():
        text = text.replace("${" + key + "}", value)
    json.loads(text)  # must be valid JSON after substitution
    return text


def _register_orchestration(client_kw: dict, region: str, *, with_athena: bool) -> dict:
    import boto3

    sns = boto3.client("sns", **client_kw)
    topic_arn = sns.create_topic(Name="airbnb-hosts-guests-alerts")["TopicArn"]
    arn = f"arn:aws:{{}}:{region}:{DUMMY_ACCOUNT}:{{}}"
    definition = render_definition(
        {
            "cluster_arn": arn.format("ecs", "cluster/airbnb-hosts-guests"),
            "task_definition_arn": arn.format("ecs", "task-definition/airbnb-hosts-guests:1"),
            # Terraform joins the list the same way: ["${subnet_ids}"] -> ["a","b"]
            "subnet_ids": 'subnet-00000000000000001","subnet-00000000000000002',
            "security_group_id": "sg-00000000000000001",
            "assign_public_ip": "ENABLED",
            "container_name": "pipeline",
            "alerts_topic_arn": topic_arn,
        }
    )
    sfn = boto3.client("stepfunctions", **client_kw)
    iam = f"arn:aws:iam::{DUMMY_ACCOUNT}:role/{{}}"  # IAM ARNs carry no region
    role = iam.format("airbnb-hosts-guests-sfn")
    sm_arn = sfn.create_state_machine(
        name="airbnb-hosts-guests-pipeline", definition=definition, roleArn=role
    )["stateMachineArn"]
    described = sfn.describe_state_machine(stateMachineArn=sm_arn)
    states = list(json.loads(described["definition"])["States"])

    scheduler = boto3.client("scheduler", **client_kw)
    scheduler.create_schedule(
        Name="airbnb-hosts-guests-monthly",
        ScheduleExpression="cron(0 6 3 * ? *)",
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={"Arn": sm_arn, "RoleArn": iam.format("airbnb-hosts-guests-scheduler")},
    )
    schedule = scheduler.get_schedule(Name="airbnb-hosts-guests-monthly")

    out = {
        "state_machine_arn": sm_arn,
        "state_machine_states": states,
        "schedule_expression": schedule["ScheduleExpression"],
        "schedule_target": schedule["Target"]["Arn"],
        "alerts_topic_arn": topic_arn,
    }
    if not with_athena:
        out["athena_bytes_scanned_cutoff"] = "skipped (Athena is not in this emulator)"
        return out
    athena = boto3.client("athena", **client_kw)
    athena.create_work_group(
        Name="airbnb-hosts-guests",
        Configuration={
            "EnforceWorkGroupConfiguration": True,
            "BytesScannedCutoffPerQuery": ATHENA_BYTES_CUTOFF,
            "ResultConfiguration": {"OutputLocation": "s3://airbnb-athena-results-emulated/"},
        },
    )
    wg = athena.get_work_group(WorkGroup="airbnb-hosts-guests")["WorkGroup"]["Configuration"]
    out["athena_bytes_scanned_cutoff"] = wg.get("BytesScannedCutoffPerQuery")
    return out


def run_smoke(
    endpoint_url: str | None = None,
    *,
    fixtures_only: bool = True,
    fixtures_dir: Path | None = None,
    workdir: Path | None = None,
    catalog: str = "glue",
) -> dict:
    """Run the AWS code paths against moto/LocalStack. Returns what was read back.

    `catalog="sql"` keeps Iceberg data and metadata on (emulated) S3 but uses the SQLite
    catalog instead of Glue, and skips Athena: LocalStack's community edition has neither.
    """
    import boto3

    from airbnb_lakehouse import bronze, ingest
    from airbnb_lakehouse.catalog import BRONZE, get_catalog
    from airbnb_lakehouse.expose import connect, expose_bronze
    from airbnb_lakehouse.quality.checks import landing_checks, run_sql_checks
    from airbnb_lakehouse.storage import get_store

    if not fixtures_only:
        raise ValueError("aws-smoke only runs on the fixture sample")
    base = load_settings()
    tmp = Path(workdir or tempfile.mkdtemp(prefix="airbnb-aws-smoke-"))
    with emulator(endpoint_url) as endpoint:
        settings = dataclasses.replace(
            base,
            data_dir=tmp,
            source="fixtures",
            fixtures_dir=fixtures_dir or base.fixtures_dir,
            storage="s3",
            catalog=catalog,
            bucket=os.environ.get("AIRBNB_SMOKE_BUCKET", "airbnb-lake-emulated"),
            s3_endpoint_url=endpoint,
            aws_region=os.environ["AWS_REGION"],
        )
        os.environ["AIRBNB_DUCKDB_PATH"] = (tmp / "warehouse" / "smoke.duckdb").as_posix()
        client_kw = {"endpoint_url": endpoint, "region_name": settings.aws_region}
        s3 = boto3.client("s3", **client_kw)
        create = {"Bucket": settings.bucket}
        if settings.aws_region != "us-east-1":
            create["CreateBucketConfiguration"] = {"LocationConstraint": settings.aws_region}
        s3.create_bucket(**create)

        store = get_store(settings)
        plan = ingest.build_plan(settings)
        ingest.write_plan(settings, plan, store)
        ingest.land_all(settings, store, plan)
        landing = landing_checks(store, plan)

        cat = get_catalog(settings)
        results = bronze.load_all(cat, store, plan)
        if catalog == "glue":
            glue = boto3.client("glue", **client_kw)
            tables = glue.get_tables(DatabaseName=BRONZE)["TableList"]
            registered = sorted(t["Name"] for t in tables)
        else:
            registered = sorted(t[-1] for t in cat.list_tables(BRONZE))
        counts = {
            name: cat.load_table(f"{BRONZE}.{name}").scan().to_arrow().num_rows
            for name in ("listings", "calendar", "reviews", "snapshots")
        }
        locations = {
            name: cat.load_table(f"{BRONZE}.{name}").metadata_location
            for name in ("listings", "calendar")
        }

        expose_bronze(settings, cat)
        con = connect(settings)
        try:
            bronze_dq = run_sql_checks(con, "bronze")
            duck_counts = {
                name: con.execute(f"SELECT count(*) FROM bronze.{name}").fetchone()[0]
                for name in ("listings", "calendar", "reviews")
            }
        finally:
            con.close()

        rerun = bronze.load_all(cat, store, plan)  # idempotency: unchanged hashes are skipped
        orchestration = _register_orchestration(
            client_kw, settings.aws_region, with_athena=catalog == "glue"
        )
        landed_keys = store.list_keys(ingest.LANDING_PREFIX)

    return {
        "endpoint": "moto (in-process)" if endpoint_url is None else endpoint_url,
        "bucket": settings.bucket,
        "snapshots": [f"{s['city']}@{s['snapshot_date']}" for s in plan["snapshots"]],
        "landed_objects": len(landed_keys),
        "landing_errors": [r.name for r in landing if r.severity == "error" and not r.passed],
        "bronze_loads": sum(1 for r in results if not r.skipped),
        "rerun_loads": sum(1 for r in rerun if not r.skipped),
        "catalog": catalog,
        "registered_tables": registered,
        "iceberg_rows": counts,
        "duckdb_rows_via_s3": duck_counts,
        "metadata_locations": locations,
        "bronze_dq_errors": [r.name for r in bronze_dq if r.severity == "error" and not r.passed],
        **orchestration,
    }
