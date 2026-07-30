import json

import boto3

from services.analytics_sink.handler import handler


def test_handler_writes_the_event_envelope_verbatim_to_s3(aws_stack, monkeypatch):
    monkeypatch.setenv("ANALYTICS_BUCKET", "analytics-test")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="analytics-test")

    event = {
        "version": "0",
        "id": "evt-1",
        "detail-type": "job.created",
        "source": "montage.pipeline",
        "account": "123456789012",
        "time": "2026-07-30T00:00:00Z",
        "region": "us-east-1",
        "detail": {"job_id": "job1", "user_id": "user-1", "status": "UPLOADING"},
    }

    handler(event)

    body = s3.get_object(Bucket="analytics-test", Key="events/evt-1.json")["Body"].read()
    assert json.loads(body) == event


def test_handler_works_without_a_job_id_in_detail(aws_stack, monkeypatch):
    monkeypatch.setenv("ANALYTICS_BUCKET", "analytics-test")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="analytics-test")

    event = {"id": "evt-2", "detail-type": "job.failed", "detail": {}}
    handler(event)

    body = s3.get_object(Bucket="analytics-test", Key="events/evt-2.json")["Body"].read()
    assert json.loads(body) == event
