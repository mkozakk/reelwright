import json
from pathlib import Path

import boto3

from services.common import dynamo
from services.cut.prepare import build_batches, handler

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"


def test_build_batches_groups_by_five():
    assert build_batches(12) == [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11]]


def test_build_batches_exact_multiple():
    assert build_batches(10) == [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9]]


def test_build_batches_empty():
    assert build_batches(0) == []


def test_handler_reads_plan_from_dynamodb_and_batches_clip_indices(aws_stack, monkeypatch):
    plan = json.loads((SAMPLE_DIR / "plan_full.json").read_text())
    job_id = "job1"

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "RENDERING",
            "edit_plan": dynamo.to_decimal(plan),
        }
    )
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])

    result = handler({"job_id": job_id})

    assert result["job_id"] == job_id
    expected = build_batches(len(plan["clips"]))
    assert [b["clip_indices"] for b in result["batches"]] == expected
    assert all(b["job_id"] == job_id for b in result["batches"])
