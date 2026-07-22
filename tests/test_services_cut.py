import json
from pathlib import Path

import boto3
import pytest

from services.common import dynamo, s3keys
from services.cut.handler import handler, run_cut

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"


def _seed_job(aws_stack, job_id: str) -> dict:
    plan = json.loads((SAMPLE_DIR / "plan_full.json").read_text())

    s3 = boto3.client("s3", region_name="us-east-1")
    src_keys = {}
    for src_id, filename in (("src1", "clip_a.mp4"), ("src2", "clip_b.mp4")):
        key = s3keys.raw_key(job_id, src_id)
        s3.upload_file(str(SAMPLE_DIR / filename), aws_stack["raw_bucket"], key)
        src_keys[src_id] = key

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "RENDERING",
            "edit_plan": dynamo.to_decimal(plan),
            "sources": {
                src_id: {"key": key, "kind": "video", "size": 1, "uploaded": True}
                for src_id, key in src_keys.items()
            },
        }
    )
    return plan


@pytest.mark.media
def test_run_cut_uploads_one_segment_per_requested_clip(aws_stack):
    _seed_job(aws_stack, "job1")

    results = run_cut(
        "job1",
        [0, 2],
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    assert [r["index"] for r in results] == [0, 2]
    assert results[0]["key"] == s3keys.work_clip_key("job1", 0)
    assert results[1]["key"] == s3keys.work_clip_key("job1", 2)

    s3 = boto3.client("s3", region_name="us-east-1")
    listing = s3.list_objects_v2(Bucket=aws_stack["work_bucket"], Prefix=s3keys.work_clips_prefix("job1"))
    keys = sorted(obj["Key"] for obj in listing["Contents"])
    assert keys == [s3keys.work_clip_key("job1", 0), s3keys.work_clip_key("job1", 2)]


@pytest.mark.media
def test_run_cut_downloads_a_reused_source_only_once(aws_stack, monkeypatch):
    _seed_job(aws_stack, "job1")

    import services.cut.handler as cut_handler

    calls: list[str] = []
    original_download = cut_handler.storage.download

    def counting_download(bucket, key, dest_dir):
        calls.append(key)
        return original_download(bucket, key, dest_dir)

    monkeypatch.setattr(cut_handler.storage, "download", counting_download)

    # clips 0 and 1 both come from src1 (plan_full.json) -- the instant-replay case
    run_cut(
        "job1",
        [0, 1],
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    assert calls == [s3keys.raw_key("job1", "src1")]


@pytest.mark.media
def test_handler_reads_env_vars_and_delegates_to_run_cut(aws_stack, monkeypatch):
    _seed_job(aws_stack, "job1")
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("RAW_BUCKET", aws_stack["raw_bucket"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])

    result = handler({"job_id": "job1", "clip_indices": [2]})
    assert result["job_id"] == "job1"
    assert [c["index"] for c in result["clips"]] == [2]
