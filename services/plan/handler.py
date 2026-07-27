from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from renderer.edit_plan.models import EditPlan
from renderer.edit_plan.validate import validate_plan
from services.common import dynamo, s3keys, storage

from . import fallback_planner


def run_plan(job_id: str, jobs_table: str, work_bucket: str) -> None:
    dynamo.update_job(jobs_table, job_id, status="PLANNING")

    job = dynamo.get_job(jobs_table, job_id)
    src_id, loudness_key = next(iter(job.analysis_keys["loudness"].items()))  # single source in Phase 3

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        local_loudness = storage.download(work_bucket, loudness_key, tmp_dir)
        loudness_data = json.loads(local_loudness.read_text())

    raw_plan = fallback_planner.build_plan(src_id, loudness_data.get("points", []), job.prefs)
    plan = validate_plan(EditPlan.model_validate(raw_plan), job.prefs)

    dynamo.update_job(jobs_table, job_id, edit_plan=plan.model_dump(), status="RENDERING")


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    run_plan(
        job_id,
        jobs_table=os.environ["JOBS_TABLE"],
        work_bucket=os.environ["WORK_BUCKET"],
    )
    return {"job_id": job_id}
