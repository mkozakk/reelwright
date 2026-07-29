from __future__ import annotations

import os

from renderer.edit_plan.models import EditPlan
from services.common import dynamo
from services.common.logging import log_job
from services.common.tracing import segment

CLIP_BATCH_SIZE = 5


def build_batches(clip_count: int, batch_size: int = CLIP_BATCH_SIZE) -> list[list[int]]:
    return [
        list(range(start, min(start + batch_size, clip_count)))
        for start in range(0, clip_count, batch_size)
    ]


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    jobs_table = os.environ["JOBS_TABLE"]

    with log_job(__name__, job_id), segment(__name__, job_id):
        dynamo.start_step(jobs_table, job_id, "cut")
        job = dynamo.get_job(jobs_table, job_id)
        plan = EditPlan.model_validate(job.edit_plan)
        batches = build_batches(len(plan.clips))

    return {
        "job_id": job_id,
        "batches": [{"job_id": job_id, "clip_indices": batch} for batch in batches],
    }
