from __future__ import annotations

import os

from services.common import semaphore

DEFAULT_RENDER_CONCURRENCY_CAP = 2


class SlotUnavailable(RuntimeError):
    pass


def acquire_handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    jobs_table = os.environ["JOBS_TABLE"]
    cap = int(os.environ.get("RENDER_CONCURRENCY_CAP", DEFAULT_RENDER_CONCURRENCY_CAP))

    if not semaphore.acquire_slot(jobs_table, job_id, cap):
        # the ASL Retry on this exception *is* the wait -- no new compute, per docs.DESIGN.md D3
        raise SlotUnavailable(f"render concurrency cap ({cap}) reached")
    return {"job_id": job_id}


def release_handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    jobs_table = os.environ["JOBS_TABLE"]
    semaphore.release_slot(jobs_table, job_id)
    return {"job_id": job_id}
