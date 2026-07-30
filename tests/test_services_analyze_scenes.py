import json
from pathlib import Path

import boto3
import pytest

from services.analyze_scenes.handler import handler, run_analyze_scenes
from services.common import dynamo, s3keys

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"


def _seed_job_with_raw_video(aws_stack, job_id: str) -> str:
    raw_key = s3keys.raw_key(job_id, "src1")
    clip_path = SAMPLE_DIR / "clip_a.mp4"

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(clip_path), aws_stack["raw_bucket"], raw_key)

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "ANALYZING",
            "sources": {
                "src1": {
                    "key": raw_key,
                    "kind": "video",
                    "size": clip_path.stat().st_size,
                    "uploaded": True,
                }
            },
        }
    )
    return raw_key


@pytest.mark.media
def test_run_analyze_scenes_writes_a_compact_json_artifact_matching_the_data_model(aws_stack):
    job_id = "job1"
    _seed_job_with_raw_video(aws_stack, job_id)

    run_analyze_scenes(
        job_id,
        "src1",
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    scenes_key = job.analysis_keys["scenes"]["src1"]
    assert scenes_key == s3keys.work_scenes_key(job_id, "src1")

    s3 = boto3.client("s3", region_name="us-east-1")
    body = s3.get_object(Bucket=aws_stack["work_bucket"], Key=scenes_key)["Body"].read()
    assert len(body) > 0

    data = json.loads(body)
    assert data["src_id"] == "src1"
    assert isinstance(data["cuts"], list)
    for cut in data["cuts"]:
        assert set(cut.keys()) == {"t", "score"}


@pytest.mark.media
def test_run_analyze_scenes_never_reads_the_source_video_from_the_work_bucket(aws_stack, monkeypatch):
    # regression guard: scdet needs real frames, which only ever live in the
    # raw bucket (the FLAC Probe extracts is audio-only) -- the work bucket
    # must only ever be written to (the scenes JSON output), never read from.
    job_id = "job1"
    _seed_job_with_raw_video(aws_stack, job_id)

    import services.analyze_scenes.handler as analyze_scenes_handler

    downloaded_buckets: list[str] = []
    original_download = analyze_scenes_handler.storage.download

    def recording_download(bucket, key, dest_dir):
        downloaded_buckets.append(bucket)
        return original_download(bucket, key, dest_dir)

    monkeypatch.setattr(analyze_scenes_handler.storage, "download", recording_download)

    run_analyze_scenes(
        job_id,
        "src1",
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    assert downloaded_buckets == [aws_stack["raw_bucket"]]
    assert aws_stack["work_bucket"] not in downloaded_buckets


@pytest.mark.media
def test_handler_reads_env_vars_and_delegates_to_run_analyze_scenes(aws_stack, monkeypatch):
    job_id = "job1"
    _seed_job_with_raw_video(aws_stack, job_id)

    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("RAW_BUCKET", aws_stack["raw_bucket"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])

    assert handler({"job_id": job_id, "src_id": "src1"}) == {"job_id": job_id, "src_id": "src1"}
    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.analysis_keys["scenes"]["src1"] == s3keys.work_scenes_key(job_id, "src1")
