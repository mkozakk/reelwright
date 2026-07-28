from __future__ import annotations

import json
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

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

USAGE = {"input_tokens": 4000, "output_tokens": 400}


def _llm_plan(summary: str = "llm montage") -> dict:
    return {
        "version": "1",
        "summary": summary,
        "clips": [{"source": "src1", "start": 0.0, "end": 2.0, "reason": "loud moment at 1.0s"}],
        "output": {"max_duration": 12.0},
    }


def _overlapping_plan() -> dict:
    return {
        "version": "1",
        "summary": "broken",
        "clips": [
            {"source": "src1", "start": 0.0, "end": 2.0, "reason": "a"},
            {"source": "src1", "start": 1.0, "end": 3.0, "reason": "b"},
        ],
        "output": {"max_duration": 12.0},
    }


class FakePlanner:
    def __init__(self, responses, model_id="amazon.nova-lite-v1:0"):
        self.responses = list(responses)
        self.model_id = model_id
        self.calls = 0

    def generate(self, messages):
        self.calls += 1
        return self.responses.pop(0), USAGE


class RaisingPlanner:
    model_id = "amazon.nova-lite-v1:0"

    def generate(self, messages):
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "Converse")


def _seed(aws_stack, job_id: str, prefs: dict | None = None) -> None:
    loudness_key = s3keys.work_loudness_key(job_id, "src1")
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=aws_stack["work_bucket"], Key=loudness_key, Body=json.dumps(LOUDNESS_ARTIFACT).encode()
    )
    boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"]).put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "ANALYZING",
            "prefs": dynamo.to_decimal(prefs or {"max_duration": 12.0}),
            "analysis_keys": {"loudness": {"src1": loudness_key}},
        }
    )


def test_llm_plan_is_used_and_costed_on_the_happy_path(aws_stack):
    _seed(aws_stack, "job1")
    planner = FakePlanner([_llm_plan()])

    run_plan("job1", aws_stack["jobs_table"], aws_stack["work_bucket"], planner=planner)

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.status == "RENDERING"
    assert job.edit_plan["summary"] == "llm montage"
    assert job.planning["source"] == "llm"
    assert job.planning["attempts"] == 1
    assert job.planning["retries"] == 0
    assert job.planning["cost_usd"] > 0


def test_invalid_plan_triggers_one_retry_then_succeeds(aws_stack):
    _seed(aws_stack, "job1")
    planner = FakePlanner([_overlapping_plan(), _llm_plan("second try")])

    run_plan("job1", aws_stack["jobs_table"], aws_stack["work_bucket"], planner=planner)

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert planner.calls == 2
    assert job.edit_plan["summary"] == "second try"
    assert job.planning["source"] == "llm"
    assert job.planning["attempts"] == 2
    assert job.planning["retries"] == 1


def test_two_invalid_plans_fall_back_to_the_no_llm_planner(aws_stack):
    _seed(aws_stack, "job1")
    planner = FakePlanner([_overlapping_plan(), _overlapping_plan()])

    run_plan("job1", aws_stack["jobs_table"], aws_stack["work_bucket"], planner=planner)

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.status == "RENDERING"
    assert job.planning["source"] == "fallback"
    assert any("loudness peak" in clip["reason"] for clip in job.edit_plan["clips"])
    EditPlan.model_validate(job.edit_plan)  # must not raise


def test_bedrock_unavailable_falls_back_without_retrying(aws_stack):
    _seed(aws_stack, "job1")

    run_plan("job1", aws_stack["jobs_table"], aws_stack["work_bucket"], planner=RaisingPlanner())

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.status == "RENDERING"
    assert job.planning["source"] == "fallback"
    assert job.planning["attempts"] == 0
    EditPlan.model_validate(job.edit_plan)


def test_run_plan_reaches_planning_status_before_the_loudness_download(aws_stack, monkeypatch):
    _seed(aws_stack, "job1")
    import services.plan.handler as plan_handler

    def failing_download(bucket, key, dest_dir):
        raise RuntimeError("simulated loudness download failure")

    monkeypatch.setattr(plan_handler.storage, "download", failing_download)

    with pytest.raises(RuntimeError):
        run_plan("job1", aws_stack["jobs_table"], aws_stack["work_bucket"], planner=FakePlanner([]))

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.status == "PLANNING"
    assert job.edit_plan is None


def test_handler_reads_env_vars_and_delegates(aws_stack, monkeypatch):
    _seed(aws_stack, "job1")
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])
    monkeypatch.setattr(
        "services.plan.handler.BedrockPlanner", lambda *a, **k: FakePlanner([_llm_plan()])
    )

    assert handler({"job_id": "job1"}) == {"job_id": "job1"}
    assert dynamo.get_job(aws_stack["jobs_table"], "job1").status == "RENDERING"
