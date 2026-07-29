from __future__ import annotations

import json
from datetime import datetime, timezone

import boto3

SOURCE = "montage.pipeline"


def publish(detail_type: str, job_id: str, **detail) -> None:
    boto3.client("events").put_events(
        Entries=[
            {
                "Source": SOURCE,
                "DetailType": detail_type,
                "Detail": json.dumps(
                    {
                        "job_id": job_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        **detail,
                    }
                ),
            }
        ]
    )
