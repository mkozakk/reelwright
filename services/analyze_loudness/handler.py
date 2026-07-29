from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from renderer.loudness import run_loudness_analysis
from services.common import dynamo, s3keys, storage
from services.common.logging import log_job
from services.common.tracing import segment


def run_analyze_loudness(job_id: str, jobs_table: str, work_bucket: str) -> None:
    dynamo.start_step(jobs_table, job_id, "loudness")
    job = dynamo.get_job(jobs_table, job_id)
    src_id, audio_key = next(iter(job.analysis_keys["audio"].items()))  # single source in Phase 3

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        local_audio = storage.download(work_bucket, audio_key, tmp_dir)

        points = run_loudness_analysis(local_audio)
        artifact = {
            "src_id": src_id,
            "sample_interval_s": 1.0,
            "points": [{"t": point.t, "level_db": point.level_db} for point in points],
        }

        out_path = tmp_dir / f"{src_id}_loudness.json"
        out_path.write_text(json.dumps(artifact))

        loudness_key = s3keys.work_loudness_key(job_id, src_id)
        storage.upload(work_bucket, loudness_key, out_path)

    dynamo.set_analysis_key(jobs_table, job_id, "loudness", {src_id: loudness_key})
    dynamo.finish_step(jobs_table, job_id, "loudness")


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    with log_job(__name__, job_id), segment(__name__, job_id):
        run_analyze_loudness(
            job_id,
            jobs_table=os.environ["JOBS_TABLE"],
            work_bucket=os.environ["WORK_BUCKET"],
        )
    return {"job_id": job_id}
