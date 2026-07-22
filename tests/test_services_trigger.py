import boto3
import pytest

from services.common import dynamo
from services.trigger.handler import handler
from services.trigger.logic import (
    TriggerEventError,
    execution_name,
    hash_prefs,
    parse_object_key,
    parse_trigger_event,
)


def _s3_event(bucket: str, key: str, etag: str) -> dict:
    return {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "detail": {"bucket": {"name": bucket}, "object": {"key": key, "etag": etag}},
    }


def test_parse_trigger_event_extracts_bucket_key_etag():
    event = _s3_event("raw-bucket", "raw/job1/src1", "etag123")
    assert parse_trigger_event(event) == ("raw-bucket", "raw/job1/src1", "etag123")


def test_parse_trigger_event_rejects_malformed_event():
    with pytest.raises(TriggerEventError):
        parse_trigger_event({"detail": {}})


def test_parse_object_key_splits_job_and_source():
    assert parse_object_key("raw/job1/src1") == ("job1", "src1")


def test_parse_object_key_rejects_unexpected_shape():
    with pytest.raises(TriggerEventError):
        parse_object_key("work/job1/audio/src1.flac")


def test_hash_prefs_is_order_independent():
    assert hash_prefs({"aspect": "9:16", "max_duration": 30}) == hash_prefs(
        {"max_duration": 30, "aspect": "9:16"}
    )
    assert hash_prefs({"aspect": "9:16"}) != hash_prefs({"aspect": "16:9"})


def test_execution_name_changes_with_any_input():
    base = execution_name("job1", "etag1", "hash1")
    assert base != execution_name("job2", "etag1", "hash1")
    assert base != execution_name("job1", "etag2", "hash1")
    assert base != execution_name("job1", "etag1", "hash2")
    assert base == execution_name("job1", "etag1", "hash1")


STATE_MACHINE_DEFINITION = """
{
  "StartAt": "Done",
  "States": {"Done": {"Type": "Pass", "End": true}}
}
"""


def test_handler_flips_status_and_starts_execution_once(aws_stack, monkeypatch):
    jobs_table = aws_stack["jobs_table"]
    raw_bucket = aws_stack["raw_bucket"]
    monkeypatch.setenv("JOBS_TABLE", jobs_table)

    sfn = boto3.client("stepfunctions", region_name="us-east-1")
    state_machine = sfn.create_state_machine(
        name="montage-pipeline",
        definition=STATE_MACHINE_DEFINITION,
        roleArn="arn:aws:iam::123456789012:role/test-role",
    )
    monkeypatch.setenv("STATE_MACHINE_ARN", state_machine["stateMachineArn"])

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(jobs_table)
    table.put_item(Item={"pk": dynamo.job_pk("job1"), "status": "UPLOADING", "prefs": {}})

    event = _s3_event(raw_bucket, "raw/job1/src1", "etag1")

    result = handler(event)
    assert result == {"job_id": "job1", "started": True}
    assert dynamo.get_job(jobs_table, "job1").status == "ANALYZING"

    executions = sfn.list_executions(stateMachineArn=state_machine["stateMachineArn"])["executions"]
    assert len(executions) == 1

    # duplicate delivery of the same S3 event must not start a second execution
    result = handler(event)
    assert result == {"job_id": "job1", "started": False, "reason": "already started"}
    executions = sfn.list_executions(stateMachineArn=state_machine["stateMachineArn"])["executions"]
    assert len(executions) == 1
