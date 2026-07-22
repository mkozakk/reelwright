from __future__ import annotations

import hashlib
import json


class TriggerEventError(ValueError):
    pass


def parse_trigger_event(event: dict) -> tuple[str, str, str]:
    """EventBridge 'S3 Object Created' event -> (bucket, key, etag)."""
    detail = event.get("detail", {})
    try:
        bucket = detail["bucket"]["name"]
        obj = detail["object"]
        key = obj["key"]
        etag = obj["etag"]
    except KeyError as exc:
        raise TriggerEventError(f"malformed S3 event: missing {exc}") from exc
    return bucket, key, etag


def parse_object_key(key: str) -> tuple[str, str]:
    """raw/<job_id>/<src_id> -> (job_id, src_id)."""
    parts = key.split("/")
    if len(parts) != 3 or parts[0] != "raw":
        raise TriggerEventError(f"unexpected object key shape: '{key}'")
    _, job_id, src_id = parts
    return job_id, src_id


def hash_prefs(prefs: dict) -> str:
    canonical = json.dumps(prefs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def execution_name(job_id: str, etag: str, prefs_hash: str) -> str:
    payload = f"{job_id}/{etag}/{prefs_hash}"
    return hashlib.sha256(payload.encode()).hexdigest()
