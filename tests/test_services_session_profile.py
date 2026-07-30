import boto3

from services.common import dynamo
from services.session_profile.handler import SessionCapExceeded, handler, run_session_profile


def _seed_job(aws_stack, job_id: str, sources: dict, prefs: dict | None = None) -> None:
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    item = {"pk": dynamo.job_pk(job_id), "status": "ANALYZING", "sources": sources}
    if prefs is not None:
        item["prefs"] = prefs
    table.put_item(Item=item)


def _video_source(duration: int, **overrides) -> dict:
    base = {"key": "raw/job1/src", "kind": "video", "size": 100, "uploaded": True, "duration": duration}
    base.update(overrides)
    return base


def test_run_session_profile_returns_video_only_items_under_the_cap(aws_stack):
    _seed_job(
        aws_stack,
        "job1",
        sources={
            "src1": _video_source(60),
            "src2": _video_source(90),
            "src3": {"key": "raw/job1/src3", "kind": "audio", "size": 100, "uploaded": True, "duration": 200},
        },
        prefs={"subtitles_enabled": False},
    )

    result = run_session_profile("job1", jobs_table=aws_stack["jobs_table"])

    assert result["video_source_items"] == [
        {"job_id": "job1", "src_id": "src1"},
        {"job_id": "job1", "src_id": "src2"},
    ]
    assert result["subtitles_enabled"] is False

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.target_profile["video_source_count"] == 2
    assert job.target_profile["audio_asset_count"] == 1
    assert job.target_profile["session_duration"] == 150.0
    assert job.status == "ANALYZING"


def test_run_session_profile_defaults_subtitles_enabled_true_when_absent(aws_stack):
    _seed_job(aws_stack, "job1", sources={"src1": _video_source(10)})

    result = run_session_profile("job1", jobs_table=aws_stack["jobs_table"])
    assert result["subtitles_enabled"] is True


def test_run_session_profile_rejects_a_session_over_the_video_duration_cap(aws_stack):
    _seed_job(
        aws_stack,
        "job1",
        sources={
            "src1": _video_source(150),
            "src2": _video_source(160),
        },
    )

    try:
        run_session_profile("job1", jobs_table=aws_stack["jobs_table"])
        assert False, "expected SessionCapExceeded"
    except SessionCapExceeded:
        pass

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.status == "FAILED"
    assert "session video duration" in job.error


def test_run_session_profile_ignores_music_file_duration_for_the_cap(aws_stack):
    # video-only cap scope (Assumption #1, docs/phases/phase-8.md task.md) --
    # a long music file must not push a small video session over the cap
    _seed_job(
        aws_stack,
        "job1",
        sources={
            "src1": _video_source(100),
            "src2": {"key": "raw/job1/src2", "kind": "audio", "size": 100, "uploaded": True, "duration": 280},
        },
    )

    result = run_session_profile("job1", jobs_table=aws_stack["jobs_table"])
    assert result["video_source_items"] == [{"job_id": "job1", "src_id": "src1"}]


def test_handler_reads_env_and_delegates(aws_stack, monkeypatch):
    _seed_job(aws_stack, "job1", sources={"src1": _video_source(10)})
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])

    result = handler({"job_id": "job1"})
    assert result["job_id"] == "job1"
    assert result["video_source_items"] == [{"job_id": "job1", "src_id": "src1"}]
