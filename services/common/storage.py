from __future__ import annotations

from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def _client():
    return boto3.client("s3")


def exists(bucket: str, key: str) -> bool:
    try:
        _client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise


def download(bucket: str, key: str, dest_dir: Path) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    local_path = dest_dir / Path(key).name
    _client().download_file(bucket, key, str(local_path))
    return local_path


def upload(bucket: str, key: str, local_path: Path) -> None:
    _client().upload_file(str(local_path), bucket, key)


def list_keys(bucket: str, prefix: str) -> list[str]:
    paginator = _client().get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return sorted(keys)
