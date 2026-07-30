from __future__ import annotations

import json
import os

import boto3

from services.common.logging import log_job
from services.common.tracing import segment

# EventBridge invokes this Lambda with the full rule-matched event envelope
# (version/id/detail-type/source/account/time/region/detail) as `event` --
# written verbatim, one object per event, no reshaping. Replaces Kinesis
# Firehose (docs/phases/phase-7.md originally specified Firehose -> Parquet,
# but that needs a per-account service subscription this account doesn't
# have; a JSON object per event is plenty at portfolio scale and Athena
# queries JSON directly via the OpenX SerDe, no format conversion needed).


def handler(event: dict, context=None) -> None:
    job_id = event.get("detail", {}).get("job_id")
    with log_job(__name__, job_id), segment(__name__, job_id):
        bucket = os.environ["ANALYTICS_BUCKET"]
        key = f"events/{event['id']}.json"
        boto3.client("s3").put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(event).encode(),
            ContentType="application/json",
        )
