from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from renderer.compile import compile_plan
from renderer.edit_plan.models import EditPlan
from renderer.ffmpeg_run import run_ffmpeg
from renderer.segments import build_concat_plan, clip_output_duration
from services.common import dynamo, s3keys, storage
from services.common.logging import get_logger
from services.common.tracing import segment


@dataclass
class RenderResult:
    output_key: str


def run_render_job(job_id: str, jobs_table: str, work_bucket: str, output_bucket: str) -> RenderResult:
    dynamo.start_step(jobs_table, job_id, "render")
    job = dynamo.get_job(jobs_table, job_id)
    plan = EditPlan.model_validate(job.edit_plan)

    if plan.subtitles.enabled:
        # word-level subtitle retiming across the cut/concat boundary isn't
        # built yet -- Phase 2's canned plan must ship with subtitles off.
        raise NotImplementedError("subtitle burn-in is not supported across cut segments yet")

    # cut_keys is a {str(clip_index): cache_key} map (services/cut/handler.py)
    # -- reading it, rather than listing a job-id-prefixed path, is what lets
    # a rerender reuse a mix of cache-hit and freshly-cut clips regardless of
    # which job originally produced each one (docs/phases/phase-7.md).
    clip_keys = [job.cut_keys[k] for k in sorted(job.cut_keys, key=int)]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        sources = {
            f"clip{i}": storage.download(work_bucket, key, tmp_dir)
            for i, key in enumerate(clip_keys)
        }

        durations = [clip_output_duration(clip) for clip in plan.clips]
        concat_plan = build_concat_plan(plan, durations)

        music_override = None
        track = plan.audio.music_track
        if track and track.startswith("user:"):
            # job-scoped asset -- resolved by looking the id up in this job's
            # own work-bucket assets, never a client-supplied path
            # (docs/DESIGN.md §10 no-file-references layer)
            asset_id = track.removeprefix("user:")
            music_override = storage.download(work_bucket, s3keys.work_asset_key(job_id, asset_id), tmp_dir)

        out_path = tmp_dir / "montage.mp4"
        command = compile_plan(concat_plan, sources, out_path, music_override=music_override)
        run_ffmpeg(command.args)

        key = s3keys.output_key(job_id)
        storage.upload(output_bucket, key, out_path)

    dynamo.update_job(jobs_table, job_id, output_key=key, status="RENDERING")
    dynamo.finish_step(jobs_table, job_id, "render")
    return RenderResult(output_key=key)


def main(job_id: str | None = None) -> int:
    job_id = job_id or os.environ["JOB_ID"]
    jobs_table = os.environ["JOBS_TABLE"]
    work_bucket = os.environ["WORK_BUCKET"]
    output_bucket = os.environ["OUTPUT_BUCKET"]

    log = get_logger(__name__, job_id)
    log.info("render started")
    try:
        with segment(__name__, job_id):
            run_render_job(job_id, jobs_table, work_bucket, output_bucket)
    except Exception as exc:
        log.exception("render failed")
        dynamo.mark_failed(jobs_table, job_id, str(exc))
        return 1
    log.info("render finished")
    return 0
