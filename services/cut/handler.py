from __future__ import annotations

import os
import tempfile
from pathlib import Path

from renderer.compile import compile_plan
from renderer.edit_plan.models import EditPlan
from renderer.ffmpeg_run import run_ffmpeg
from renderer.segments import build_segment_plan, clip_output_duration
from services.common import dynamo, s3keys, storage


def run_cut(
    job_id: str,
    clip_indices: list[int],
    jobs_table: str,
    raw_bucket: str,
    work_bucket: str,
) -> list[dict]:
    job = dynamo.get_job(jobs_table, job_id)
    plan = EditPlan.model_validate(job.edit_plan)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        source_cache: dict[str, Path] = {}
        results: list[dict] = []

        for index in clip_indices:
            clip = plan.clips[index]
            if clip.source not in source_cache:
                source_ref = job.sources[clip.source]
                source_cache[clip.source] = storage.download(raw_bucket, source_ref.key, tmp_dir)
            local_source = source_cache[clip.source]

            segment_plan = build_segment_plan(plan, index)
            out_path = tmp_dir / f"{index:03d}.mp4"
            command = compile_plan(segment_plan, {clip.source: local_source}, out_path)
            run_ffmpeg(command.args)

            key = s3keys.work_clip_key(job_id, index)
            storage.upload(work_bucket, key, out_path)
            results.append({"index": index, "key": key, "duration": clip_output_duration(clip)})

        return results


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    clips = run_cut(
        job_id,
        event["clip_indices"],
        jobs_table=os.environ["JOBS_TABLE"],
        raw_bucket=os.environ["RAW_BUCKET"],
        work_bucket=os.environ["WORK_BUCKET"],
    )
    return {"job_id": job_id, "clips": clips}
