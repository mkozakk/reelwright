from pathlib import Path

import boto3
import pytest

from services.common import dynamo, s3keys
from services.probe.handler import ProbeRejected, handler, run_probe

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"


def _seed_job(
    aws_stack, job_id: str, *, sources: dict | None = None, declared_size: int | None = None, prefs: dict | None = None
) -> None:
    if sources is None:
        real_size = declared_size if declared_size is not None else (SAMPLE_DIR / "clip_a.mp4").stat().st_size
        raw_key = s3keys.raw_key(job_id, "src1")
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.upload_file(str(SAMPLE_DIR / "clip_a.mp4"), aws_stack["raw_bucket"], raw_key)
        sources = {"src1": {"key": raw_key, "kind": "video", "size": real_size, "uploaded": True}}

    item = {"pk": dynamo.job_pk(job_id), "status": "ANALYZING", "sources": sources}
    if prefs is not None:
        item["prefs"] = prefs

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(Item=item)


@pytest.mark.media
def test_run_probe_extracts_audio_and_leaves_status_as_analyzing(aws_stack):
    # Phase 3: ANALYZING already covers the whole Analyze branch (Probe +
    # the 3 parallel Analyze Lambdas) -- Probe no longer flips status itself.
    _seed_job(aws_stack, "job1")

    run_probe(
        "job1",
        "src1",
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.status == "ANALYZING"
    audio_key = job.analysis_keys["audio"]["src1"]
    assert audio_key == s3keys.work_audio_key("job1", "src1")
    assert job.sources["src1"].kind == "video"
    assert job.sources["src1"].width == 1280
    assert 7.5 < job.sources["src1"].duration < 8.5

    s3 = boto3.client("s3", region_name="us-east-1")
    head = s3.head_object(Bucket=aws_stack["work_bucket"], Key=audio_key)
    assert head["ContentLength"] > 0


@pytest.mark.media
def test_run_probe_handles_an_audio_only_source(aws_stack):
    raw_key = s3keys.raw_key("job1", "src2")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(SAMPLE_DIR / "music_a.mp3"), aws_stack["raw_bucket"], raw_key)
    size = (SAMPLE_DIR / "music_a.mp3").stat().st_size

    _seed_job(
        aws_stack,
        "job1",
        sources={"src2": {"key": raw_key, "kind": "audio", "size": size, "uploaded": True}},
    )

    run_probe(
        "job1",
        "src2",
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.sources["src2"].kind == "audio"
    assert job.sources["src2"].duration > 0
    # audio-only sources never enter Analyze -- no analysis_keys entry
    assert "audio" not in job.analysis_keys or "src2" not in job.analysis_keys.get("audio", {})

    asset_key = s3keys.work_asset_key("job1", "src2")
    s3.head_object(Bucket=aws_stack["work_bucket"], Key=asset_key)  # raises if missing


@pytest.mark.media
def test_run_probe_rejects_and_marks_failed_without_uploading_audio(aws_stack):
    # declared size wildly exceeds the cap, even though the uploaded fixture is tiny --
    # validate_probe trusts the declared size from the job record, not a re-measurement.
    from renderer.probe import MAX_FILE_BYTES

    _seed_job(aws_stack, "job1", declared_size=MAX_FILE_BYTES + 1)

    with pytest.raises(ProbeRejected):
        run_probe(
            "job1",
            "src1",
            jobs_table=aws_stack["jobs_table"],
            raw_bucket=aws_stack["raw_bucket"],
            work_bucket=aws_stack["work_bucket"],
        )

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.status == "FAILED"
    assert "file size" in job.error

    s3 = boto3.client("s3", region_name="us-east-1")
    listing = s3.list_objects_v2(Bucket=aws_stack["work_bucket"], Prefix=f"work/job1/")
    assert listing.get("KeyCount", 0) == 0


@pytest.mark.media
def test_run_probe_rejects_declared_kind_mismatch(aws_stack):
    # an audio file declared as "video" at presign (content-type spoofing) is
    # rejected rather than silently probed as if it were the declared kind
    raw_key = s3keys.raw_key("job1", "src1")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(SAMPLE_DIR / "music_a.mp3"), aws_stack["raw_bucket"], raw_key)
    size = (SAMPLE_DIR / "music_a.mp3").stat().st_size

    _seed_job(
        aws_stack,
        "job1",
        sources={"src1": {"key": raw_key, "kind": "video", "size": size, "uploaded": True}},
    )

    with pytest.raises(ProbeRejected) as excinfo:
        run_probe(
            "job1",
            "src1",
            jobs_table=aws_stack["jobs_table"],
            raw_bucket=aws_stack["raw_bucket"],
            work_bucket=aws_stack["work_bucket"],
        )
    assert any("declared kind" in e for e in excinfo.value.errors)


@pytest.mark.media
def test_handler_reads_env_vars_and_delegates_to_run_probe(aws_stack, monkeypatch):
    _seed_job(aws_stack, "job1")

    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("RAW_BUCKET", aws_stack["raw_bucket"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])

    assert handler({"job_id": "job1", "src_id": "src1"}) == {"job_id": "job1", "src_id": "src1"}
    assert dynamo.get_job(aws_stack["jobs_table"], "job1").status == "ANALYZING"
