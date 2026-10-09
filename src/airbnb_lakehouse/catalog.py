"""Iceberg catalog factory.

* `sql`  - pyiceberg's SqlCatalog on a SQLite file. It is the local stand-in for the AWS Glue
  Data Catalog: same namespaces, same tables, same Iceberg metadata files. With
  `AIRBNB_STORAGE=s3` its warehouse is on S3 (used against LocalStack, whose community edition
  has no Glue).
* `glue` - pyiceberg's GlueCatalog. Pointed at moto (tests) or at real Glue + S3 when the
  pipeline runs as an ECS task in AWS.
"""

from __future__ import annotations

import os

from pyiceberg.catalog import Catalog

from airbnb_lakehouse.config import Settings

BRONZE = os.environ.get("AIRBNB_BRONZE_NAMESPACE", "bronze")


def _s3_props(settings: Settings) -> dict[str, str]:
    props = {"s3.region": settings.aws_region}
    if settings.s3_endpoint_url:  # moto / LocalStack
        props["s3.endpoint"] = settings.s3_endpoint_url
    for env, prop in (
        ("AWS_ACCESS_KEY_ID", "s3.access-key-id"),
        ("AWS_SECRET_ACCESS_KEY", "s3.secret-access-key"),
        ("AWS_SESSION_TOKEN", "s3.session-token"),
    ):
        if os.environ.get(env):
            props[prop] = os.environ[env]
    return props


def get_catalog(settings: Settings) -> Catalog:
    if settings.catalog == "sql":
        from pyiceberg.catalog.sql import SqlCatalog

        settings.lake_dir.mkdir(parents=True, exist_ok=True)
        props = _s3_props(settings) if settings.lake_on_s3 else {}
        return SqlCatalog(
            "lake",
            uri=f"sqlite:///{(settings.lake_dir / 'catalog.db').as_posix()}",
            warehouse=settings.warehouse_uri,
            **props,
        )
    if settings.catalog == "glue":
        from pyiceberg.catalog.glue import GlueCatalog

        props = {
            "warehouse": settings.warehouse_uri,
            "glue.region": settings.aws_region,
            **_s3_props(settings),
        }
        if settings.s3_endpoint_url:
            props["glue.endpoint"] = settings.s3_endpoint_url
        return GlueCatalog("lake", **props)
    raise ValueError(f"AIRBNB_CATALOG must be 'sql' or 'glue', got {settings.catalog!r}")
