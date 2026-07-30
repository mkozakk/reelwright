from __future__ import annotations

import json
import os

import boto3

from services.common import dynamo
from services.common.logging import log_job
from services.common.tracing import segment

from .logic import execution_name, hash_prefs, parse_object_key, parse_trigger_event


def handler(event: dict, context=None) -> dict:
    jobs_table = os.environ["JOBS_TABLE"]
    state_machine_arn = os.environ["STATE_MACHINE_ARN"]

    bucket, key, etag = parse_trigger_event(event)
    job_id, src_id = parse_object_key(key)
    del bucket, src_id  # not needed beyond routing -- the job record has everything else

    with log_job(__name__, job_id) as log, segment(__name__, job_id):
        job = dynamo.get_job(jobs_table, job_id)
        flipped = dynamo.conditional_status_flip(jobs_table, job_id, "UPLOADING", "ANALYZING")
        if not flipped:
            log.info("already started")
            return {"job_id": job_id, "started": False, "reason": "already started"}

        name = execution_name(job_id, etag, hash_prefs(job.prefs))
        sfn = boto3.client("stepfunctions")
        try:
            sfn.start_execution(
                stateMachineArn=state_machine_arn,
                name=name,
                input=json.dumps({"job_id": job_id, "mode": "new"}),
            )
        except sfn.exceptions.ExecutionAlreadyExists:
            pass

    return {"job_id": job_id, "started": True}
