import json
from pathlib import Path

import boto3
import pytest

from renderer.probe import extract_audio_flac
from services.analyze_loudness.handler import handler, run_analyze_loudness
from services.common import dynamo, s3keys

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def _seed_job_with_real_audio(aws_stack, job_id: str) -> str:
    audio_key = s3keys.work_audio_key(job_id, "src1")
    local_flac = extract_audio_flac(SAMPLE_DIR / "clip_a.mp4", OUT_DIR / f"{job_id}_loudness_input.flac")

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(local_flac), aws_stack["work_bucket"], audio_key)

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "ANALYZING",
            "analysis_keys": {"audio": {"src1": audio_key}},
        }
    )
    return audio_key


@pytest.mark.media
def test_run_analyze_loudness_writes_a_compact_json_artifact_matching_the_data_model(aws_stack):
    job_id = "job1"
    _seed_job_with_real_audio(aws_stack, job_id)

    run_analyze_loudness(job_id, "src1", jobs_table=aws_stack["jobs_table"], work_bucket=aws_stack["work_bucket"])

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    loudness_key = job.analysis_keys["loudness"]["src1"]
    assert loudness_key == s3keys.work_loudness_key(job_id, "src1")

    s3 = boto3.client("s3", region_name="us-east-1")
    body = s3.get_object(Bucket=aws_stack["work_bucket"], Key=loudness_key)["Body"].read()
    assert len(body) > 0

    data = json.loads(body)
    assert data["src_id"] == "src1"
    assert isinstance(data["points"], list)
    assert len(data["points"]) >= 1
    for point in data["points"]:
        assert set(point.keys()) == {"t", "level_db"}


@pytest.mark.media
def test_run_analyze_loudness_leaves_the_pre_existing_audio_key_untouched(aws_stack):
    # writing the "loudness" leaf must not disturb Probe's earlier "audio" leaf
    job_id = "job1"
    audio_key = _seed_job_with_real_audio(aws_stack, job_id)

    run_analyze_loudness(job_id, "src1", jobs_table=aws_stack["jobs_table"], work_bucket=aws_stack["work_bucket"])

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.analysis_keys["audio"]["src1"] == audio_key


@pytest.mark.media
def test_handler_reads_env_vars_and_delegates_to_run_analyze_loudness(aws_stack, monkeypatch):
    job_id = "job1"
    _seed_job_with_real_audio(aws_stack, job_id)

    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])

    assert handler({"job_id": job_id, "src_id": "src1"}) == {"job_id": job_id, "src_id": "src1"}
    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.analysis_keys["loudness"]["src1"] == s3keys.work_loudness_key(job_id, "src1")


@pytest.mark.media
def test_run_analyze_loudness_supports_a_second_source_on_the_same_job(aws_stack):
    # regression for the set_analysis_key whole-category-overwrite race
    # (docs/phases/phase-8.md task.md T2/T14): two sources writing the
    # "loudness" category on the same job must not clobber each other
    job_id = "job1"
    _seed_job_with_real_audio(aws_stack, job_id)

    audio_key2 = s3keys.work_audio_key(job_id, "src2")
    local_flac = extract_audio_flac(SAMPLE_DIR / "clip_b.mp4", OUT_DIR / f"{job_id}_loudness_input2.flac")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(local_flac), aws_stack["work_bucket"], audio_key2)
    dynamo.set_analysis_key(aws_stack["jobs_table"], job_id, "audio", "src2", audio_key2)

    run_analyze_loudness(job_id, "src1", jobs_table=aws_stack["jobs_table"], work_bucket=aws_stack["work_bucket"])
    run_analyze_loudness(job_id, "src2", jobs_table=aws_stack["jobs_table"], work_bucket=aws_stack["work_bucket"])

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert set(job.analysis_keys["loudness"]) == {"src1", "src2"}
    assert job.analysis_keys["loudness"]["src1"] == s3keys.work_loudness_key(job_id, "src1")
    assert job.analysis_keys["loudness"]["src2"] == s3keys.work_loudness_key(job_id, "src2")
