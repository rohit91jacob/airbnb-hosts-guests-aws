"""Runtime settings, read once from environment variables (and `.env` via the Makefile).

Every setting has a working local default, so `airbnb run` needs no configuration at all.
The AWS-shaped settings (bucket, endpoint, catalog type) only matter when the S3 or Glue
backends are selected; locally they point at the filesystem stand-ins or at moto/LocalStack.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CITIES = ("amsterdam", "austin", "singapore")
USER_AGENT = (
    "airbnb-hosts-guests-aws/0.1 (+https://github.com/rohit91jacob/airbnb-hosts-guests-aws)"
)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default.resolve()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cities: tuple[str, ...]
    snapshots_per_city: int
    source: str  # "insideairbnb" (live download) or "fixtures" (local directory)
    fixtures_dir: Path
    storage: str  # "local" (filesystem laid out like S3) or "s3" (boto3)
    bucket: str
    s3_endpoint_url: str | None
    aws_region: str
    catalog: str  # "sql" (SQLite-backed Iceberg catalog) or "glue"
    http_timeout: float = 120.0
    extra: dict[str, str] = field(default_factory=dict)

    # --- derived paths -----------------------------------------------------------------
    @property
    def object_store_root(self) -> Path:
        """Root of the local S3 stand-in: <data_dir>/s3/<bucket>/<key>."""
        return self.data_dir / "s3" / self.bucket

    @property
    def lake_dir(self) -> Path:
        return self.data_dir / "lake"

    @property
    def lake_on_s3(self) -> bool:
        """Iceberg data/metadata live on S3 (Glue catalog, or SQLite catalog + S3 storage)."""
        return self.catalog == "glue" or self.storage == "s3"

    @property
    def warehouse_uri(self) -> str:
        if self.lake_on_s3:
            return f"s3://{self.bucket}/warehouse"
        # pyiceberg on Windows needs a plain absolute path (a file:// URI becomes /C:/...).
        return (self.lake_dir / "warehouse").as_posix()

    @property
    def duckdb_path(self) -> Path:
        return _path("AIRBNB_DUCKDB_PATH", self.data_dir / "warehouse" / "airbnb.duckdb")

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def dbt_dir(self) -> Path:
        return REPO_ROOT / "dbt"


def load_settings() -> Settings:
    cities = tuple(
        c.strip().lower() for c in _env("AIRBNB_CITIES", ",".join(DEFAULT_CITIES)).split(",")
    )
    return Settings(
        data_dir=_path("AIRBNB_DATA_DIR", REPO_ROOT / "data"),
        cities=tuple(c for c in cities if c),
        snapshots_per_city=int(_env("AIRBNB_SNAPSHOTS_PER_CITY", "2")),
        source=_env("AIRBNB_SOURCE", "insideairbnb"),
        fixtures_dir=_path(
            "AIRBNB_FIXTURES_DIR", REPO_ROOT / "tests" / "fixtures" / "insideairbnb"
        ),
        storage=_env("AIRBNB_STORAGE", "local"),
        bucket=_env("AIRBNB_LAKE_BUCKET", "airbnb-lake-local"),
        s3_endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
        aws_region=_env("AWS_REGION", _env("AWS_DEFAULT_REGION", "eu-west-1")),
        catalog=_env("AIRBNB_CATALOG", "sql"),
        http_timeout=float(_env("AIRBNB_HTTP_TIMEOUT", "120")),
    )
