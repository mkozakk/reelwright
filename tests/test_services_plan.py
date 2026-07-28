from __future__ import annotations

import json
from pathlib import Path

import boto3
import pytest

from renderer.edit_plan.models import EditPlan
from services.common import dynamo, s3keys
from services.plan.handler import handler, run_plan

REPO_ROOT = Path(__file__).resolve().parents[1]

LOUDNESS_ARTIFACT = {
    "src_id": "src1",
    "sample_interval_s": 1.0,
    "points": [
        {"t": 0.0, "level_db": -30.0},
        {"t": 1.0, "level_db": -10.0},
        {"t": 2.0, "level_db": -25.0},
        {"t": 3.0, "level_db": -28.0},
    ],
}


def _seed_job_with_loudness_artifact(aws_stack, job_id: str, prefs: dict | None = None) -> str:
    loudness_key = s3keys.work_loudness_key(job_id, "src1")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(
        Bucket=aws_stack["work_bucket"],
        Key=loudness_key,
        Body=json.dumps(LOUDNESS_ARTIFACT).encode(),
    )

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "ANALYZING",
            "prefs": dynamo.to_decimal(prefs or {}),
            "analysis_keys": {"loudness": {"src1": loudness_key}},
        }
    )
    return loudness_key


def test_run_plan_produces_a_schema_valid_fallback_plan_and_marks_rendering(aws_stack):
    job_id = "job1"
    _seed_job_with_loudness_artifact(aws_stack, job_id, prefs={"max_duration": 12.0})

    run_plan(job_id, jobs_table=aws_stack["jobs_table"], work_bucket=aws_stack["work_bucket"])

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "RENDERING"
    plan = EditPlan.model_validate(job.edit_plan)  # must not raise
    assert len(plan.clips) >= 1
    assert any("loudness peak" in clip.reason for clip in plan.clips)


def test_run_plan_reaches_planning_status_before_the_loudness_download_completes(aws_stack, monkeypatch):
    # proves the two-phase status write (PLANNING then RENDERING) is real,
    # not a single combined write at the end.
    job_id = "job1"
    _seed_job_with_loudness_artifact(aws_stack, job_id)

    import services.plan.handler as plan_handler

    def failing_download(bucket, key, dest_dir):
        raise RuntimeError("simulated loudness download failure")

    monkeypatch.setattr(plan_handler.storage, "download", failing_download)

    with pytest.raises(RuntimeError):
        run_plan(job_id, jobs_table=aws_stack["jobs_table"], work_bucket=aws_stack["work_bucket"])

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "PLANNING"
    assert job.edit_plan is None


def test_handler_reads_env_vars_and_delegates_to_run_plan(aws_stack, monkeypatch):
    job_id = "job1"
    _seed_job_with_loudness_artifact(aws_stack, job_id)

    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])

    assert handler({"job_id": job_id}) == {"job_id": job_id}
    assert dynamo.get_job(aws_stack["jobs_table"], job_id).status == "RENDERING"
