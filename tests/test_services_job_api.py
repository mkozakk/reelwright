import json

import boto3
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services.common import dynamo, s3keys, session_caps
from services.job_api import logic
from services.job_api.handler import handler


def _event(
    method,
    *,
    body=None,
    ip="203.0.113.1",
    job_id=None,
    path=None,
    sub="user-1",
    email="user@example.com",
):
    if path is None:
        path = f"/jobs/{job_id}/complete" if job_id else "/jobs"
    return {
        "requestContext": {
            "http": {"method": method, "sourceIp": ip, "path": path},
            "authorizer": {"jwt": {"claims": {"sub": sub, "email": email}}},
        },
        "body": None if body is None else json.dumps(body),
        "pathParameters": {"id": job_id} if job_id else None,
    }


def _valid_create(**overrides):
    body = {"files": [{"content_type": "video/mp4", "size": 10 * 1024 * 1024}]}
    body.update(overrides)
    return body


def _throwaway_keypair() -> bytes:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _set_api_env(monkeypatch, aws_stack):
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("RAW_BUCKET", aws_stack["raw_bucket"])


def _state_machine_arn() -> str:
    sfn = boto3.client("stepfunctions", region_name="us-east-1")
    definition = json.dumps({"StartAt": "Noop", "States": {"Noop": {"Type": "Pass", "End": True}}})
    return sfn.create_state_machine(
        name="pipeline-test", definition=definition, roleArn="arn:aws:iam::123456789012:role/fake"
    )["stateMachineArn"]


def test_parse_body_rejects_empty_and_non_object():
    with pytest.raises(logic.ApiError):
        logic.parse_body(None)
    with pytest.raises(logic.ApiError):
        logic.parse_body("not json")
    with pytest.raises(logic.ApiError):
        logic.parse_body("[1, 2]")


def test_validate_create_request_normalises_a_good_request():
    out = logic.validate_create_request(_valid_create(prefs={"vibe": "  upbeat  ", "aspect": "9:16"}))
    assert out["files"] == [{"content_type": "video/mp4", "kind": "video", "size": 10 * 1024 * 1024}]
    assert out["prefs"] == {"vibe": "upbeat", "aspect": "9:16"}


def test_validate_create_request_accepts_a_real_editorial_brief_up_to_the_cap():
    brief = (
        "open on the first scene from video 1, use the audio I uploaded as "
        "music, keep it gentle with minimal effects"
    )
    assert len(brief) <= logic.MAX_VIBE_LEN
    out = logic.validate_create_request(_valid_create(prefs={"vibe": brief}))
    assert out["prefs"]["vibe"] == brief


def test_validate_create_request_rejects_a_vibe_over_the_cap():
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(prefs={"vibe": "x" * (logic.MAX_VIBE_LEN + 1)}))


def test_validate_create_request_accepts_mixed_video_and_audio_files():
    out = logic.validate_create_request(
        _valid_create(
            files=[
                {"content_type": "video/mp4", "size": 10 * 1024 * 1024},
                {"content_type": "audio/mpeg", "size": 1024 * 1024},
            ]
        )
    )
    assert [f["kind"] for f in out["files"]] == ["video", "audio"]


def test_validate_create_request_rejects_bad_content_type():
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(files=[{"content_type": "application/zip", "size": 100}]))


def test_validate_create_request_enforces_size_cap():
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(
            _valid_create(files=[{"content_type": "video/mp4", "size": logic.MAX_FILE_BYTES + 1}])
        )
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(files=[{"content_type": "video/mp4", "size": 0}]))


def test_validate_create_request_rejects_empty_and_oversized_file_lists():
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(files=[]))
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(
            _valid_create(files=[{"content_type": "video/mp4", "size": 100}] * (session_caps.MAX_FILES + 1))
        )


def test_validate_create_request_rejects_all_audio_sessions():
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(files=[{"content_type": "audio/mpeg", "size": 100}]))


def test_validate_create_request_rejects_unknown_pref():
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(prefs={"speed": "fast"}))


def test_validate_create_request_rejects_bad_aspect_and_duration():
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(prefs={"aspect": "4:3"}))
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(prefs={"max_duration": 999}))


def test_upload_plan_switches_to_multipart_above_the_threshold():
    assert logic.wants_multipart(logic.MULTIPART_THRESHOLD) is False
    assert logic.wants_multipart(logic.MULTIPART_THRESHOLD + 1) is True
    assert logic.part_count(logic.PART_SIZE) == 1
    assert logic.part_count(logic.PART_SIZE + 1) == 2


def test_validate_complete_request_normalises_and_sorts_parts():
    out = logic.validate_complete_request(
        {
            "src_id": "src1",
            "upload_id": "u1",
            "parts": [{"part_number": 2, "etag": "b"}, {"part_number": 1, "etag": "a"}],
        }
    )
    assert out["src_id"] == "src1"
    assert out["upload_id"] == "u1"
    assert out["parts"] == [{"PartNumber": 1, "ETag": "a"}, {"PartNumber": 2, "ETag": "b"}]


def test_validate_complete_request_rejects_bad_input():
    with pytest.raises(logic.ApiError):
        logic.validate_complete_request({"upload_id": "u1", "parts": [{"part_number": 1, "etag": "a"}]})
    with pytest.raises(logic.ApiError):
        logic.validate_complete_request({"src_id": "src1", "parts": [{"part_number": 1, "etag": "a"}]})
    with pytest.raises(logic.ApiError):
        logic.validate_complete_request({"src_id": "src1", "upload_id": "u1", "parts": []})
    with pytest.raises(logic.ApiError):
        logic.validate_complete_request(
            {"src_id": "src1", "upload_id": "u1", "parts": [{"part_number": 0, "etag": "a"}]}
        )
    with pytest.raises(logic.ApiError):
        logic.validate_complete_request({"src_id": "src1", "upload_id": "u1", "parts": [{"part_number": 1}]})


def test_src_ids_generates_declaration_order_ids():
    assert logic.src_ids(3) == ["src1", "src2", "src3"]


def test_build_job_item_shapes_the_record_for_multiple_files():
    created = logic.now()
    request = logic.validate_create_request(
        _valid_create(
            files=[
                {"content_type": "video/mp4", "size": 100},
                {"content_type": "audio/mpeg", "size": 200},
            ],
            prefs={"vibe": "calm"},
        )
    )
    item = logic.build_job_item("abc", request["files"], request, created, "user-1")
    assert item["pk"] == "JOB#abc"
    assert item["user_id"] == "user-1"
    assert item["status"] == "UPLOADING"
    assert item["created_at"] == created.isoformat()
    assert item["sources"] == {
        "src1": {"key": s3keys.raw_key("abc", "src1"), "kind": "video", "size": 100, "uploaded": False},
        "src2": {"key": s3keys.raw_key("abc", "src2"), "kind": "audio", "size": 200, "uploaded": False},
    }
    assert item["prefs"] == {"vibe": "calm"}
    assert item["ttl"] > int(created.timestamp())


def test_options_preflight_returns_204(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    resp = handler(_event("OPTIONS"))
    assert resp["statusCode"] == 204
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"


def test_create_job_writes_record_and_returns_one_presigned_url_per_file(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    resp = handler(
        _event(
            "POST",
            body=_valid_create(
                files=[
                    {"content_type": "video/mp4", "size": 10 * 1024 * 1024},
                    {"content_type": "audio/mpeg", "size": 1024 * 1024},
                ],
                prefs={"vibe": "punchy"},
            ),
        )
    )

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["status"] == "UPLOADING"
    job_id = body["job_id"]
    assert [s["src_id"] for s in body["sources"]] == ["src1", "src2"]
    assert [s["kind"] for s in body["sources"]] == ["video", "audio"]
    for source in body["sources"]:
        assert source["expires_in"] == logic.UPLOAD_URL_TTL
        assert s3keys.raw_key(job_id, source["src_id"]) in source["upload_url"]

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "UPLOADING"
    assert job.prefs == {"vibe": "punchy"}
    assert job.sources["src1"].uploaded is False
    assert job.sources["src2"].kind == "audio"


def test_create_job_publishes_a_job_created_event(aws_stack, monkeypatch):
    from services.job_api import handler as handler_mod

    published = []
    monkeypatch.setattr(handler_mod.events, "publish", lambda *a, **kw: published.append((a, kw)))
    _set_api_env(monkeypatch, aws_stack)

    resp = handler(_event("POST", body=_valid_create()))
    job_id = json.loads(resp["body"])["job_id"]

    [(args, kwargs)] = published
    assert args[0] == "job.created"
    assert args[1] == job_id
    assert kwargs == {"user_id": "user-1", "status": "UPLOADING"}


def test_create_job_uses_multipart_for_large_files(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    size = logic.MULTIPART_THRESHOLD + 3 * logic.PART_SIZE
    resp = handler(_event("POST", body=_valid_create(files=[{"content_type": "video/mp4", "size": size}])))

    assert resp["statusCode"] == 201
    source = json.loads(resp["body"])["sources"][0]
    assert source["upload_type"] == "multipart"
    assert source["upload_id"]
    assert source["part_size"] == logic.PART_SIZE
    assert len(source["parts"]) == logic.part_count(size)
    assert [p["part_number"] for p in source["parts"]] == list(range(1, len(source["parts"]) + 1))
    assert "uploadId" in source["parts"][0]["url"]


def test_complete_upload_finalises_a_multipart_object(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    job_id = "job1"
    key = s3keys.raw_key(job_id, "src1")
    s3 = boto3.client("s3", region_name="us-east-1")
    boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"]).put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "user_id": "user-1",
            "status": "UPLOADING",
            "created_at": "2026-07-29T00:00:00+00:00",
            "sources": {"src1": {"key": key, "kind": "video", "size": 7, "uploaded": False}},
        }
    )

    upload_id = s3.create_multipart_upload(Bucket=aws_stack["raw_bucket"], Key=key)["UploadId"]
    part = s3.upload_part(
        Bucket=aws_stack["raw_bucket"], Key=key, UploadId=upload_id, PartNumber=1, Body=b"payload"
    )

    resp = handler(
        _event(
            "POST",
            body={"src_id": "src1", "upload_id": upload_id, "parts": [{"part_number": 1, "etag": part["ETag"]}]},
            job_id=job_id,
        )
    )
    assert resp["statusCode"] == 200

    head = s3.head_object(Bucket=aws_stack["raw_bucket"], Key=key)
    assert head["ContentLength"] == len(b"payload")


def test_complete_upload_rejects_unknown_src_id(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    job_id = "job1"
    boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"]).put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "user_id": "user-1",
            "status": "UPLOADING",
            "created_at": "2026-07-29T00:00:00+00:00",
            "sources": {"src1": {"key": "raw/job1/src1", "kind": "video", "size": 7, "uploaded": False}},
        }
    )

    resp = handler(
        _event(
            "POST",
            body={"src_id": "src9", "upload_id": "u1", "parts": [{"part_number": 1, "etag": "a"}]},
            job_id=job_id,
        )
    )
    assert resp["statusCode"] == 400


def test_create_job_rejects_invalid_body(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    resp = handler(_event("POST", body={"files": [{"content_type": "video/mp4"}]}))
    assert resp["statusCode"] == 400
    assert "error" in json.loads(resp["body"])


def test_per_ip_daily_cap_returns_429(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    monkeypatch.setattr(logic, "IP_DAILY_CAP", 1)

    first = handler(_event("POST", body=_valid_create(), ip="198.51.100.7", sub="user-a"))
    assert first["statusCode"] == 201
    second = handler(_event("POST", body=_valid_create(), ip="198.51.100.7", sub="user-b"))
    assert second["statusCode"] == 429

    # a different address is unaffected
    other = handler(_event("POST", body=_valid_create(), ip="198.51.100.8", sub="user-c"))
    assert other["statusCode"] == 201


def test_per_user_daily_cap_returns_429(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    monkeypatch.setattr(logic, "USER_DAILY_CAP", 1)

    first = handler(_event("POST", body=_valid_create(), ip="198.51.100.7", sub="user-a"))
    assert first["statusCode"] == 201
    second = handler(_event("POST", body=_valid_create(), ip="198.51.100.8", sub="user-a"))
    assert second["statusCode"] == 429

    # a different account, same IP subnet, is unaffected
    other = handler(_event("POST", body=_valid_create(), ip="198.51.100.9", sub="user-b"))
    assert other["statusCode"] == 201


def test_get_job_returns_status_and_404_for_missing(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk("job1"),
            "user_id": "user-1",
            "status": "PLANNING",
            "created_at": "2026-07-29T00:00:00+00:00",
            "prefs": {"vibe": "calm"},
        }
    )

    resp = handler(_event("GET", job_id="job1"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {
        "job_id": "job1",
        "status": "PLANNING",
        "created_at": "2026-07-29T00:00:00+00:00",
        "prefs": {"vibe": "calm"},
    }

    missing = handler(_event("GET", job_id="nope"))
    assert missing["statusCode"] == 404


def test_get_job_hides_other_users_jobs_as_404(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk("job1"),
            "user_id": "user-a",
            "status": "PLANNING",
            "created_at": "2026-07-29T00:00:00+00:00",
            "prefs": {},
        }
    )

    resp = handler(_event("GET", job_id="job1", sub="user-b"))
    assert resp["statusCode"] == 404


def test_list_jobs_returns_only_the_caller_s_jobs(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    a = handler(_event("POST", body=_valid_create(), sub="user-a"))
    b = handler(_event("POST", body=_valid_create(), sub="user-b"))
    a_job_id = json.loads(a["body"])["job_id"]

    resp = handler(_event("GET", sub="user-a"))
    assert resp["statusCode"] == 200
    jobs = json.loads(resp["body"])["jobs"]
    assert [j["job_id"] for j in jobs] == [a_job_id]
    assert json.loads(b["body"])["job_id"] not in [j["job_id"] for j in jobs]


def test_get_job_signs_output_urls_when_done(aws_stack, monkeypatch, requires_rsa_sha1_signing):
    _set_api_env(monkeypatch, aws_stack)
    monkeypatch.setenv("CLOUDFRONT_DOMAIN", "cdn.example.com")
    monkeypatch.setenv("CLOUDFRONT_KEY_PAIR_ID", "KEYPAIRID")
    monkeypatch.setenv("CLOUDFRONT_PRIVATE_KEY_PEM", _throwaway_keypair().decode())

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk("job1"),
            "user_id": "user-1",
            "status": "DONE",
            "created_at": "2026-07-29T00:00:00+00:00",
            "prefs": {},
            "output_key": s3keys.output_key("job1"),
            "thumbnail_key": s3keys.thumbnail_key("job1"),
            "edit_plan": {"clips": [{"reason": "the punchline"}]},
        }
    )

    resp = handler(_event("GET", job_id="job1"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "Signature=" in body["output_url"]
    assert "Signature=" in body["thumbnail_url"]
    assert body["expires_in"] == logic.PLAYBACK_URL_TTL
    assert body["edit_plan"]["clips"][0]["reason"] == "the punchline"


def _seed_uploading_job(aws_stack, job_id: str, sources: dict, user_id: str = "user-1") -> None:
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "user_id": user_id,
            "status": "UPLOADING",
            "created_at": "2026-07-29T00:00:00+00:00",
            "prefs": {},
            "sources": sources,
        }
    )


def _upload_object(aws_stack, key: str, body: bytes) -> None:
    boto3.client("s3", region_name="us-east-1").put_object(Bucket=aws_stack["raw_bucket"], Key=key, Body=body)


def test_start_verifies_uploads_and_starts_the_execution(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    monkeypatch.setenv("STATE_MACHINE_ARN", _state_machine_arn())
    job_id = "job1"
    payload = b"x" * 100
    _seed_uploading_job(
        aws_stack,
        job_id,
        {
            "src1": {"key": s3keys.raw_key(job_id, "src1"), "kind": "video", "size": len(payload), "uploaded": False},
            "src2": {"key": s3keys.raw_key(job_id, "src2"), "kind": "audio", "size": len(payload), "uploaded": False},
        },
    )
    _upload_object(aws_stack, s3keys.raw_key(job_id, "src1"), payload)
    _upload_object(aws_stack, s3keys.raw_key(job_id, "src2"), payload)

    resp = handler(_event("POST", job_id=job_id, path=f"/jobs/{job_id}/start"))
    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])
    assert body == {"job_id": job_id, "status": "ANALYZING"}

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "ANALYZING"
    assert job.sources["src1"].uploaded is True
    assert job.sources["src2"].uploaded is True

    sfn = boto3.client("stepfunctions", region_name="us-east-1")
    state_machine_arn = boto3.client("stepfunctions", region_name="us-east-1").list_state_machines()[
        "stateMachines"
    ][0]["stateMachineArn"]
    executions = sfn.list_executions(stateMachineArn=state_machine_arn)["executions"]
    assert len(executions) == 1


def test_start_rejects_when_an_upload_is_missing(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    monkeypatch.setenv("STATE_MACHINE_ARN", _state_machine_arn())
    job_id = "job1"
    payload = b"x" * 100
    _seed_uploading_job(
        aws_stack,
        job_id,
        {
            "src1": {"key": s3keys.raw_key(job_id, "src1"), "kind": "video", "size": len(payload), "uploaded": False},
            "src2": {"key": s3keys.raw_key(job_id, "src2"), "kind": "video", "size": len(payload), "uploaded": False},
        },
    )
    _upload_object(aws_stack, s3keys.raw_key(job_id, "src1"), payload)
    # src2 never uploaded

    resp = handler(_event("POST", job_id=job_id, path=f"/jobs/{job_id}/start"))
    assert resp["statusCode"] == 409
    body = json.loads(resp["body"])
    assert body["missing_or_mismatched"] == ["src2"]

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "UPLOADING"


def test_start_rejects_when_size_mismatches(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    monkeypatch.setenv("STATE_MACHINE_ARN", _state_machine_arn())
    job_id = "job1"
    _seed_uploading_job(
        aws_stack,
        job_id,
        {"src1": {"key": s3keys.raw_key(job_id, "src1"), "kind": "video", "size": 999, "uploaded": False}},
    )
    _upload_object(aws_stack, s3keys.raw_key(job_id, "src1"), b"short")

    resp = handler(_event("POST", job_id=job_id, path=f"/jobs/{job_id}/start"))
    assert resp["statusCode"] == 409


def test_start_is_idempotent_on_double_call(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    monkeypatch.setenv("STATE_MACHINE_ARN", _state_machine_arn())
    job_id = "job1"
    payload = b"x" * 100
    _seed_uploading_job(
        aws_stack,
        job_id,
        {"src1": {"key": s3keys.raw_key(job_id, "src1"), "kind": "video", "size": len(payload), "uploaded": False}},
    )
    _upload_object(aws_stack, s3keys.raw_key(job_id, "src1"), payload)

    first = handler(_event("POST", job_id=job_id, path=f"/jobs/{job_id}/start"))
    assert first["statusCode"] == 202

    second = handler(_event("POST", job_id=job_id, path=f"/jobs/{job_id}/start"))
    assert second["statusCode"] == 200
    assert json.loads(second["body"]) == {"job_id": job_id, "status": "ANALYZING"}

    sfn = boto3.client("stepfunctions", region_name="us-east-1")
    state_machine_arn = sfn.list_state_machines()["stateMachines"][0]["stateMachineArn"]
    executions = sfn.list_executions(stateMachineArn=state_machine_arn)["executions"]
    assert len(executions) == 1


def test_start_hides_other_users_jobs_as_404(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    monkeypatch.setenv("STATE_MACHINE_ARN", _state_machine_arn())
    job_id = "job1"
    _seed_uploading_job(
        aws_stack,
        job_id,
        {"src1": {"key": s3keys.raw_key(job_id, "src1"), "kind": "video", "size": 1, "uploaded": False}},
        user_id="user-a",
    )

    resp = handler(_event("POST", job_id=job_id, path=f"/jobs/{job_id}/start", sub="user-b"))
    assert resp["statusCode"] == 404
