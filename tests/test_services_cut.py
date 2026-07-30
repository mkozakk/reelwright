import json
from pathlib import Path

import boto3
import pytest

from renderer.edit_plan.models import EditPlan
from renderer.edit_plan.validate import EditPlanValidationError, SourceBounds, validate_plan
from services.common import cutcache, dynamo, s3keys
from services.cut.handler import handler, run_cut

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"


def _expected_key(job_id: str, plan: dict, clip: dict) -> str:
    profile = cutcache.profile_key(plan["output"]["aspect"], plan["output"]["resolution"])
    source_key = s3keys.raw_key(job_id, clip["source"])
    return s3keys.work_cut_cache_key(
        cutcache.cache_key(source_key, clip["start"], clip["end"], clip.get("speed", 1.0), profile)
    )


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
    plan = _seed_job(aws_stack, "job1")

    results = run_cut(
        "job1",
        [0, 2],
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    expected0 = _expected_key("job1", plan, plan["clips"][0])
    expected2 = _expected_key("job1", plan, plan["clips"][2])
    assert [r["index"] for r in results] == [0, 2]
    assert results[0]["key"] == expected0
    assert results[1]["key"] == expected2

    s3 = boto3.client("s3", region_name="us-east-1")
    listing = s3.list_objects_v2(Bucket=aws_stack["work_bucket"], Prefix="work/cache/")
    keys = sorted(obj["Key"] for obj in listing["Contents"])
    assert keys == sorted([expected0, expected2])

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.cut_keys == {"0": expected0, "2": expected2}


@pytest.mark.media
def test_run_cut_skips_ffmpeg_on_a_cache_hit(aws_stack, monkeypatch):
    _seed_job(aws_stack, "job1")

    run_cut(
        "job1",
        [0],
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    import services.cut.handler as cut_handler

    calls: list[str] = []
    original_run_ffmpeg = cut_handler.run_ffmpeg

    def counting_run_ffmpeg(args):
        calls.append(args)
        return original_run_ffmpeg(args)

    monkeypatch.setattr(cut_handler, "run_ffmpeg", counting_run_ffmpeg)

    # a rerender re-invokes Cut for the same job/clip -- same source key,
    # same params -- hitting the content-addressed key cut on the first pass
    results = run_cut(
        "job1",
        [0],
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    assert calls == []
    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.cut_keys["0"] == results[0]["key"]


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
def test_an_unknown_source_never_reaches_cut_because_validate_plan_rejects_it_first(aws_stack):
    # Regression for the class of bug ADR-2 closes (docs/phases/phase-8.md):
    # job.sources[clip.source] in run_cut below still has no guard and would
    # KeyError on an unresolvable source (proven by the second half of this
    # test) -- but the actual boundary, validate_plan, now rejects such a
    # plan before it can ever become job.edit_plan, so that KeyError path is
    # provably unreachable through the normal plan -> render pipeline.
    plan = _seed_job(aws_stack, "job1")
    bad_plan = {**plan, "clips": [{**plan["clips"][0], "source": "src9"}]}
    sources = {
        "src1": SourceBounds(kind="video", duration=100.0),
        "src2": SourceBounds(kind="video", duration=100.0),
    }

    with pytest.raises(EditPlanValidationError) as excinfo:
        validate_plan(EditPlan.model_validate(bad_plan), sources=sources)
    assert any("unknown source" in e for e in excinfo.value.errors)

    # meanwhile, run_cut itself has no such guard -- feeding it the rejected
    # plan directly (bypassing the boundary, which the normal flow never
    # does) still raises the underlying KeyError this class of bug is about
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.update_item(
        Key={"pk": dynamo.job_pk("job1")},
        UpdateExpression="SET edit_plan = :p",
        ExpressionAttributeValues={":p": dynamo.to_decimal(bad_plan)},
    )
    with pytest.raises(KeyError):
        run_cut(
            "job1",
            [0],
            jobs_table=aws_stack["jobs_table"],
            raw_bucket=aws_stack["raw_bucket"],
            work_bucket=aws_stack["work_bucket"],
        )


@pytest.mark.media
def test_handler_reads_env_vars_and_delegates_to_run_cut(aws_stack, monkeypatch):
    _seed_job(aws_stack, "job1")
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("RAW_BUCKET", aws_stack["raw_bucket"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])

    result = handler({"job_id": "job1", "clip_indices": [2]})
    assert result["job_id"] == "job1"
    assert [c["index"] for c in result["clips"]] == [2]
