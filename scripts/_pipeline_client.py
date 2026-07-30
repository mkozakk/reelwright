# shared by smoke_test.py and load_test.py -- not a standalone entrypoint.
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

import boto3

from services.common import dynamo, s3keys
from services.common.models import JobRecord
from services.trigger.logic import execution_name, hash_prefs

REPO_ROOT = Path(__file__).resolve().parents[1]
TF_DIR = REPO_ROOT / "infra" / "envs" / "dev"
SAMPLE_CLIP = REPO_ROOT / "assets" / "sample" / "clip_a.mp4"
POLL_INTERVAL_SECONDS = 10


def terraform_outputs() -> dict:
    result = subprocess.run(
        ["terraform", "output", "-json"], cwd=TF_DIR, capture_output=True, text=True, check=True
    )
    return {k: v["value"] for k, v in json.loads(result.stdout).items()}


def seed_and_upload(job_id: str, outputs: dict, prefs: dict) -> str:
    raw_key = s3keys.raw_key(job_id, "src1")
    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(outputs["jobs_table"])

    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "UPLOADING",
            "prefs": prefs,
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
    s3.upload_file(str(SAMPLE_CLIP), outputs["raw_bucket"], raw_key)
    return s3.head_object(Bucket=outputs["raw_bucket"], Key=raw_key)["ETag"].strip('"')


def poll_until_terminal(
    jobs_table: str,
    job_id: str,
    timeout_s: int,
    interval_s: int = POLL_INTERVAL_SECONDS,
    on_status: Callable[[str], None] | None = None,
) -> JobRecord:
    deadline = time.monotonic() + timeout_s
    last_status = None
    while time.monotonic() < deadline:
        job = dynamo.get_job(jobs_table, job_id)
        if job.status != last_status:
            last_status = job.status
            if on_status:
                on_status(job.status)
        if job.status in ("DONE", "FAILED"):
            return job
        time.sleep(interval_s)
    raise TimeoutError(f"job '{job_id}' did not reach a terminal status within {timeout_s}s")


def execution_result_url(state_machine_arn: str, job_id: str, etag: str, prefs: dict) -> str:
    name = execution_name(job_id, etag, hash_prefs(prefs))
    execution_arn = state_machine_arn.replace(":stateMachine:", ":execution:") + f":{name}"
    sfn = boto3.client("stepfunctions")
    execution = sfn.describe_execution(executionArn=execution_arn)
    return json.loads(execution["output"])["url"]
