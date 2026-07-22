from pathlib import Path

import boto3
import pytest

from renderer.compile import compile_plan
from renderer.edit_plan.validate import load_plan
from renderer.ffmpeg_run import run_ffmpeg
from renderer.probe import probe_file
from renderer.segments import build_segment_plan, clip_output_duration
from services.common import dynamo, s3keys
from services.render.main import main, run_render_job

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"


def _seed_cut_segments(aws_stack, job_id: str, plan_path: Path, subtitles_enabled: bool, tmp_path: Path):
    plan = load_plan(plan_path)
    plan.subtitles.enabled = subtitles_enabled
    sources = {"src1": SAMPLE_DIR / "clip_a.mp4", "src2": SAMPLE_DIR / "clip_b.mp4"}

    s3 = boto3.client("s3", region_name="us-east-1")
    for i, clip in enumerate(plan.clips):
        segment_plan = build_segment_plan(plan, i)
        out_path = tmp_path / f"seg_{job_id}_{i:03d}.mp4"
        command = compile_plan(segment_plan, {clip.source: sources[clip.source]}, out_path)
        run_ffmpeg(command.args)
        s3.upload_file(str(out_path), aws_stack["work_bucket"], s3keys.work_clip_key(job_id, i))

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "RENDERING",
            "edit_plan": dynamo.to_decimal(plan.model_dump()),
        }
    )
    return plan


@pytest.mark.media
def test_run_render_job_concatenates_cut_segments_into_a_montage(aws_stack, tmp_path):
    plan = _seed_cut_segments(aws_stack, "job1", SAMPLE_DIR / "plan_transitions.json", False, tmp_path)

    result = run_render_job(
        "job1",
        jobs_table=aws_stack["jobs_table"],
        work_bucket=aws_stack["work_bucket"],
        output_bucket=aws_stack["output_bucket"],
    )

    assert result.output_key == s3keys.output_key("job1")
    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.output_key == result.output_key

    local_out = tmp_path / "downloaded.mp4"
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.download_file(aws_stack["output_bucket"], result.output_key, str(local_out))
    assert local_out.stat().st_size > 0

    durations = [clip_output_duration(c) for c in plan.clips]
    expected = sum(durations) - plan.clips[0].transition_out.duration
    probed = probe_file(local_out)
    assert probed.duration == pytest.approx(expected, abs=0.3)


@pytest.mark.media
def test_run_render_job_refuses_plans_with_subtitles_enabled(aws_stack, tmp_path):
    _seed_cut_segments(aws_stack, "job1", SAMPLE_DIR / "plan_transitions.json", True, tmp_path)

    with pytest.raises(NotImplementedError):
        run_render_job(
            "job1",
            jobs_table=aws_stack["jobs_table"],
            work_bucket=aws_stack["work_bucket"],
            output_bucket=aws_stack["output_bucket"],
        )


@pytest.mark.media
def test_main_marks_job_failed_on_error_and_returns_nonzero(aws_stack, tmp_path, monkeypatch):
    _seed_cut_segments(aws_stack, "job1", SAMPLE_DIR / "plan_transitions.json", True, tmp_path)

    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])
    monkeypatch.setenv("OUTPUT_BUCKET", aws_stack["output_bucket"])

    assert main("job1") == 1
    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.status == "FAILED"
    assert "subtitle" in job.error
