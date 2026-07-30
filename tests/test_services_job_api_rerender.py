import json

import boto3

from services.common import dynamo
from services.job_api.handler import handler

SAMPLE_PLAN = {
    "version": "1",
    "clips": [{"source": "src1", "start": 0.0, "end": 2.0, "reason": "cold open"}],
    "output": {"aspect": "16:9", "resolution": "1080p", "max_duration": 30},
}


def _event(method, *, body=None, job_id=None, path=None, sub="user-1"):
    if path is None:
        path = f"/jobs/{job_id}/rerender" if job_id else "/jobs"
    return {
        "requestContext": {
            "http": {"method": method, "sourceIp": "203.0.113.1", "path": path},
            "authorizer": {"jwt": {"claims": {"sub": sub, "email": "user@example.com"}}},
        },
        "body": None if body is None else json.dumps(body),
        "pathParameters": {"id": job_id} if job_id else None,
    }


def _set_api_env(monkeypatch, aws_stack):
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("RAW_BUCKET", aws_stack["raw_bucket"])


def _valid_create(**overrides):
    body = {"files": [{"content_type": "video/mp4", "size": 10 * 1024 * 1024}]}
    body.update(overrides)
    return body


def _state_machine_arn() -> str:
    sfn = boto3.client("stepfunctions", region_name="us-east-1")
    role_arn = "arn:aws:iam::123456789012:role/fake"
    definition = json.dumps({"StartAt": "Noop", "States": {"Noop": {"Type": "Pass", "End": True}}})
    return sfn.create_state_machine(
        name="pipeline-test", definition=definition, roleArn=role_arn
    )["stateMachineArn"]


def _set_rerender_env(monkeypatch, aws_stack):
    _set_api_env(monkeypatch, aws_stack)
    monkeypatch.setenv("STATE_MACHINE_ARN", _state_machine_arn())


def _seed_job(aws_stack, job_id: str, user_id: str = "user-1") -> None:
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "user_id": user_id,
            "status": "DONE",
            "created_at": "2026-07-29T00:00:00+00:00",
            "prefs": {},
            "sources": {
                "src1": {
                    "key": f"raw/{job_id}/src1",
                    "kind": "video",
                    "size": 100,
                    "uploaded": True,
                    "duration": 10,
                }
            },
        }
    )


def test_rerender_validates_updates_and_starts_an_execution(aws_stack, monkeypatch):
    _set_rerender_env(monkeypatch, aws_stack)
    _seed_job(aws_stack, "job1")

    resp = handler(
        _event("POST", job_id="job1", path="/jobs/job1/rerender", body=SAMPLE_PLAN)
    )
    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])
    assert body == {"job_id": "job1", "status": "RENDERING"}

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.status == "RENDERING"
    assert job.edit_plan["clips"][0]["reason"] == "cold open"


def test_rerender_rejects_an_invalid_plan(aws_stack, monkeypatch):
    _set_rerender_env(monkeypatch, aws_stack)
    _seed_job(aws_stack, "job1")

    resp = handler(
        _event("POST", job_id="job1", path="/jobs/job1/rerender", body={"clips": []})
    )
    assert resp["statusCode"] == 400


def test_rerender_rejects_a_plan_referencing_an_unknown_source(aws_stack, monkeypatch):
    # ADR-2 (docs/phases/phase-8.md): validate_plan now enforces source
    # bounds on the rerender path too, not just fresh plans
    _set_rerender_env(monkeypatch, aws_stack)
    _seed_job(aws_stack, "job1")

    bad_plan = {**SAMPLE_PLAN, "clips": [{**SAMPLE_PLAN["clips"][0], "source": "src9"}]}
    resp = handler(_event("POST", job_id="job1", path="/jobs/job1/rerender", body=bad_plan))
    assert resp["statusCode"] == 400


def test_rerender_applies_prefs_field_authority(aws_stack, monkeypatch):
    # pre-existing gap closed alongside the sources= threading (Risk #4,
    # docs/phases/phase-8.md task.md): a stored aspect pref must overwrite
    # whatever aspect the submitted plan carries, same as a fresh plan.
    _set_rerender_env(monkeypatch, aws_stack)
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk("job1"),
            "user_id": "user-1",
            "status": "DONE",
            "created_at": "2026-07-29T00:00:00+00:00",
            "prefs": {"aspect": "9:16"},
            "sources": {
                "src1": {
                    "key": "raw/job1/src1",
                    "kind": "video",
                    "size": 100,
                    "uploaded": True,
                    "duration": 10,
                }
            },
        }
    )

    resp = handler(_event("POST", job_id="job1", path="/jobs/job1/rerender", body=SAMPLE_PLAN))
    assert resp["statusCode"] == 202

    job = dynamo.get_job(aws_stack["jobs_table"], "job1")
    assert job.edit_plan["output"]["aspect"] == "9:16"


def test_rerender_hides_other_users_jobs_as_404(aws_stack, monkeypatch):
    _set_rerender_env(monkeypatch, aws_stack)
    _seed_job(aws_stack, "job1", user_id="user-a")

    resp = handler(
        _event("POST", job_id="job1", path="/jobs/job1/rerender", body=SAMPLE_PLAN, sub="user-b")
    )
    assert resp["statusCode"] == 404


def test_rerender_counts_against_the_daily_quota(aws_stack, monkeypatch):
    from services.job_api import logic

    _set_rerender_env(monkeypatch, aws_stack)
    monkeypatch.setattr(logic, "USER_DAILY_CAP", 1)
    _seed_job(aws_stack, "job1")

    # spend the day's single slot on an ordinary job creation first
    created = handler(_event("POST", body=_valid_create()))
    assert created["statusCode"] == 201

    resp = handler(
        _event("POST", job_id="job1", path="/jobs/job1/rerender", body=SAMPLE_PLAN)
    )
    assert resp["statusCode"] == 429
