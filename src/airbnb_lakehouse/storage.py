"""Object storage for the landing zone.

Two interchangeable backends behind one small interface:

* `LocalObjectStore` lays files out exactly like S3 keys under `<data_dir>/s3/<bucket>/`.
  This is the default for local runs: no emulator process needed.
* `S3ObjectStore` uses boto3. With `AWS_ENDPOINT_URL` set it talks to moto (tests) or
  LocalStack (CI); without it, to real S3 (the gated deploy path only).

Writes are atomic in both backends (temp file + rename locally, single PUT on S3), so a
crashed download never leaves a half-written object that a later run would trust.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from airbnb_lakehouse.config import Settings


class ObjectStore(Protocol):
    uri_prefix: str

    def exists(self, key: str) -> bool: ...
    def put_file(self, key: str, path: Path) -> None: ...
    def put_json(self, key: str, payload: dict) -> None: ...
    def get_json(self, key: str) -> dict | None: ...
    def local_path(self, key: str) -> Path: ...
    def list_keys(self, prefix: str) -> list[str]: ...


class LocalObjectStore:
    def __init__(self, root: Path, bucket: str):
        self.root = root
        self.uri_prefix = f"s3://{bucket}/"
        root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def put_file(self, key: str, path: Path) -> None:
        dest = self._p(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copyfile(path, tmp)
        tmp.replace(dest)

    def put_json(self, key: str, payload: dict) -> None:
        dest = self._p(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(dest)

    def get_json(self, key: str) -> dict | None:
        p = self._p(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def local_path(self, key: str) -> Path:
        return self._p(key)

    def list_keys(self, prefix: str) -> list[str]:
        base = self._p(prefix)
        if not base.exists():
            return []
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in base.rglob("*")
            if p.is_file() and not p.name.endswith(".tmp")
        )


class S3ObjectStore:
    def __init__(self, bucket: str, endpoint_url: str | None, region: str, cache_dir: Path):
        import boto3

        self.bucket = bucket
        self.uri_prefix = f"s3://{bucket}/"
        self.client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)
        self.cache_dir = cache_dir

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def put_file(self, key: str, path: Path) -> None:
        self.client.upload_file(str(path), self.bucket, key)

    def put_json(self, key: str, payload: dict) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode()
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType="application/json"
        )

    def get_json(self, key: str) -> dict | None:
        if not self.exists(key):
            return None
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return json.loads(obj["Body"].read())

    def local_path(self, key: str) -> Path:
        """Download (once) to a local cache so DuckDB can read it."""
        dest = self.cache_dir / self.bucket / key
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dest.parent)
            os.close(fd)
            self.client.download_file(self.bucket, key, tmp)
            Path(tmp).replace(dest)
        return dest

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return sorted(keys)


def get_store(settings: Settings) -> ObjectStore:
    if settings.storage == "s3":
        return S3ObjectStore(
            settings.bucket,
            settings.s3_endpoint_url,
            settings.aws_region,
            settings.data_dir / "s3-cache",
        )
    if settings.storage != "local":
        raise ValueError(f"AIRBNB_STORAGE must be 'local' or 's3', got {settings.storage!r}")
    return LocalObjectStore(settings.object_store_root, settings.bucket)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
