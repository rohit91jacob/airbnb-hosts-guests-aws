"""CLI contract: exit codes and the S3 publish path (SYNTHETIC inputs, moto in-process)."""

from __future__ import annotations

import gzip

import boto3
from moto import mock_aws

from airbnb_lakehouse import ingest
from airbnb_lakehouse.cli import main
from airbnb_lakehouse.config import load_settings
from airbnb_lakehouse.report.site import publish_site
from tests.conftest import make_snapshot_dir, synthetic_listing, write_synthetic_catalog


def test_dq_gate_exit_code_is_3(clean_env, monkeypatch):
    fx = clean_env / "fixtures"
    make_snapshot_dir(fx, "testville", "2026-01-02", [synthetic_listing(1, 10)], nights=3)
    write_synthetic_catalog(fx, [("testville", "2026-01-02")])
    monkeypatch.setenv("AIRBNB_SOURCE", "fixtures")
    monkeypatch.setenv("AIRBNB_FIXTURES_DIR", str(fx))
    assert main(["discover"]) == 0
    assert main(["ingest"]) == 0
    assert main(["dq", "--layer", "landing"]) == 0

    s = load_settings()
    snap = ingest.iab.Snapshot.from_json(ingest.read_plan(s)["snapshots"][0])
    landed = s.object_store_root / ingest.file_key(snap, "calendar")
    with gzip.open(landed, "wt", encoding="utf-8") as f:
        f.write("listing_id\n1\n")  # date and available columns missing
    assert main(["dq", "--layer", "landing"]) == 3


def test_unknown_storage_backend_is_a_plain_failure(clean_env, monkeypatch):
    monkeypatch.setenv("AIRBNB_STORAGE", "ftp")
    assert main(["ingest"]) == 1


@mock_aws
def test_publish_site_uploads_every_file_with_content_types(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    s3 = boto3.client("s3", region_name="eu-west-1")
    s3.create_bucket(
        Bucket="site-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"}
    )
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html></html>", encoding="utf-8")
    (site / "summary.json").write_text("{}", encoding="utf-8")
    keys = publish_site(load_settings(), site, "site-bucket")
    assert keys == ["site/index.html", "site/summary.json"]
    head = s3.head_object(Bucket="site-bucket", Key="site/index.html")
    assert head["ContentType"].startswith("text/html")
