from __future__ import annotations

import os
import tempfile
from pathlib import Path

from renderer.compile import compile_plan
from renderer.edit_plan.models import EditPlan
from renderer.ffmpeg_run import run_ffmpeg
from renderer.segments import build_segment_plan, clip_output_duration
from services.common import cutcache, dynamo, s3keys, storage
from services.common.logging import log_job
from services.common.tracing import segment


def run_cut(
    job_id: str,
    clip_indices: list[int],
    jobs_table: str,
    raw_bucket: str,
    work_bucket: str,
) -> list[dict]:
    # if_not_exists in start_step means calling it here too is safe even
    # though cut/prepare.py already started "cut" -- this batch just
    # doesn't move the start time, and run_cut stays independently testable
    # without going through prepare first.
    dynamo.start_step(jobs_table, job_id, "cut")
    job = dynamo.get_job(jobs_table, job_id)
    plan = EditPlan.model_validate(job.edit_plan)
    profile = cutcache.profile_key(plan.output.aspect, plan.output.resolution)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        source_cache: dict[str, Path] = {}
        results: list[dict] = []

        for index in clip_indices:
            clip = plan.clips[index]
            source_ref = job.sources[clip.source]
            key = s3keys.work_cut_cache_key(
                cutcache.cache_key(source_ref.key, clip.start, clip.end, clip.speed, profile)
            )

            if not storage.exists(work_bucket, key):
                if clip.source not in source_cache:
                    source_cache[clip.source] = storage.download(
                        raw_bucket, source_ref.key, tmp_dir
                    )
                local_source = source_cache[clip.source]

                segment_plan = build_segment_plan(plan, index)
                out_path = tmp_dir / f"{index:03d}.mp4"
                command = compile_plan(segment_plan, {clip.source: local_source}, out_path)
                run_ffmpeg(command.args)
                storage.upload(work_bucket, key, out_path)

            dynamo.set_cut_key(jobs_table, job_id, index, key)
            results.append({"index": index, "key": key, "duration": clip_output_duration(clip)})

        dynamo.finish_step(jobs_table, job_id, "cut")
        return results


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    with log_job(__name__, job_id), segment(__name__, job_id):
        clips = run_cut(
            job_id,
            event["clip_indices"],
            jobs_table=os.environ["JOBS_TABLE"],
            raw_bucket=os.environ["RAW_BUCKET"],
            work_bucket=os.environ["WORK_BUCKET"],
        )
    return {"job_id": job_id, "clips": clips}
