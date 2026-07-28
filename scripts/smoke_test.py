#!/usr/bin/env python3
# uploads assets/sample/clip_a.mp4 against real AWS, waits for the pipeline,
# downloads the result. Reads bucket/table names from `terraform output -json`.
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.common import dynamo, s3keys  # noqa: E402
from services.trigger.logic import execution_name, hash_prefs  # noqa: E402

SAMPLE_CLIP = REPO_ROOT / "assets" / "sample" / "clip_a.mp4"
TF_DIR = REPO_ROOT / "infra" / "envs" / "dev"
POLL_INTERVAL_SECONDS = 10
TIMEOUT_SECONDS = 20 * 60

# explicit, not relying on Probe's absent-key default -- exercises the
# Transcribe branch deterministically rather than leaving it to the default
PREFS = {"subtitles_enabled": True}


def terraform_outputs() -> dict:
    result = subprocess.run(
        ["terraform", "output", "-json"], cwd=TF_DIR, capture_output=True, text=True, check=True
    )
    return {k: v["value"] for k, v in json.loads(result.stdout).items()}


def main() -> int:
    if not SAMPLE_CLIP.exists():
        print(f"missing sample clip: {SAMPLE_CLIP}", file=sys.stderr)
        return 1

    outputs = terraform_outputs()
    jobs_table = outputs["jobs_table"]
    raw_bucket = outputs["raw_bucket"]
    state_machine_arn = outputs["state_machine_arn"]

    job_id = f"smoke-{uuid.uuid4().hex[:8]}"
    raw_key = s3keys.raw_key(job_id, "src1")

    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(jobs_table)

    print(f"job_id={job_id}")
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "UPLOADING",
            "prefs": PREFS,
            "sources": {
                "src1": {
                    "key": raw_key,
                    "kind": "video",
                    "size": SAMPLE_CLIP.stat().st_size,
                    "uploaded": True,
                }
            },
        }
    )

    print(f"uploading {SAMPLE_CLIP.name} -> s3://{raw_bucket}/{raw_key}")
    s3.upload_file(str(SAMPLE_CLIP), raw_bucket, raw_key)
    etag = s3.head_object(Bucket=raw_bucket, Key=raw_key)["ETag"].strip('"')

    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_status = None
    while time.monotonic() < deadline:
        job = dynamo.get_job(jobs_table, job_id)
        if job.status != last_status:
            print(f"status={job.status}")
            last_status = job.status
        if job.status in ("DONE", "FAILED"):
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        print("timed out waiting for the pipeline", file=sys.stderr)
        return 1

    if job.status == "FAILED":
        print(f"job failed: {job.error}", file=sys.stderr)
        return 1

    print(f"output_key={job.output_key}")
    print(f"thumbnail_key={job.thumbnail_key}")

    exec_name = execution_name(job_id, etag, hash_prefs(PREFS))
    execution_arn = state_machine_arn.replace(":stateMachine:", ":execution:") + f":{exec_name}"
    sfn = boto3.client("stepfunctions")
    execution = sfn.describe_execution(executionArn=execution_arn)
    result = json.loads(execution["output"])
    signed_url = result["url"]
    print(f"signed_url={signed_url}")

    dest = REPO_ROOT / "out" / f"{job_id}.mp4"
    dest.parent.mkdir(exist_ok=True)
    urllib.request.urlretrieve(signed_url, dest)
    print(f"downloaded -> {dest} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
