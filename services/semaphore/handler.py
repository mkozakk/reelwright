from __future__ import annotations

import os

from services.common import dynamo, semaphore
from services.common.logging import get_logger, log_job

DEFAULT_RENDER_CONCURRENCY_CAP = 2


class SlotUnavailable(RuntimeError):
    pass


def acquire_handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    jobs_table = os.environ["JOBS_TABLE"]
    cap = int(os.environ.get("RENDER_CONCURRENCY_CAP", DEFAULT_RENDER_CONCURRENCY_CAP))

    # start_step is idempotent (if_not_exists) across the repeated ASL
    # invocations below, so started_at reflects when the wait began, not
    # each retry -- log_job would misreport SlotUnavailable as a failure
    # every ~10s while queued, so this uses get_logger directly instead.
    log = get_logger(__name__, job_id)
    dynamo.start_step(jobs_table, job_id, "render_wait")
    if not semaphore.acquire_slot(jobs_table, job_id, cap):
        # the ASL Retry on this exception *is* the wait -- no new compute, per docs.DESIGN.md D3
        log.info("render slot unavailable, cap=%d", cap)
        raise SlotUnavailable(f"render concurrency cap ({cap}) reached")
    dynamo.finish_step(jobs_table, job_id, "render_wait")
    log.info("render slot acquired")
    return {"job_id": job_id}


def release_handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    jobs_table = os.environ["JOBS_TABLE"]
    with log_job(__name__, job_id):
        semaphore.release_slot(jobs_table, job_id)
    return {"job_id": job_id}
