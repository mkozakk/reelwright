from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from renderer.scenes import run_scene_analysis
from services.common import dynamo, s3keys, storage
from services.common.logging import log_job


def run_analyze_scenes(job_id: str, jobs_table: str, raw_bucket: str, work_bucket: str) -> None:
    dynamo.start_step(jobs_table, job_id, "scenes")
    job = dynamo.get_job(jobs_table, job_id)
    src_id, source = next(iter(job.sources.items()))  # single source in Phase 3

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # scdet needs real frames -- the raw bucket is the only place they
        # live (Probe's FLAC extract is audio-only), never the work bucket.
        local_video = storage.download(raw_bucket, source.key, tmp_dir)

        cuts = run_scene_analysis(local_video)
        artifact = {
            "src_id": src_id,
            "cuts": [{"t": cut.t, "score": cut.score} for cut in cuts],
        }

        out_path = tmp_dir / f"{src_id}_scenes.json"
        out_path.write_text(json.dumps(artifact))

        scenes_key = s3keys.work_scenes_key(job_id, src_id)
        storage.upload(work_bucket, scenes_key, out_path)

    dynamo.set_analysis_key(jobs_table, job_id, "scenes", {src_id: scenes_key})
    dynamo.finish_step(jobs_table, job_id, "scenes")


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    with log_job(__name__, job_id):
        run_analyze_scenes(
            job_id,
            jobs_table=os.environ["JOBS_TABLE"],
            raw_bucket=os.environ["RAW_BUCKET"],
            work_bucket=os.environ["WORK_BUCKET"],
        )
    return {"job_id": job_id}
