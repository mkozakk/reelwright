import json

import boto3
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services.common import dynamo, s3keys
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
    body = {"content_type": "video/mp4", "size": 10 * 1024 * 1024}
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


# --- logic (pure) -----------------------------------------------------------


def test_parse_body_rejects_empty_and_non_object():
    with pytest.raises(logic.ApiError):
        logic.parse_body(None)
    with pytest.raises(logic.ApiError):
        logic.parse_body("not json")
    with pytest.raises(logic.ApiError):
        logic.parse_body("[1, 2]")


def test_validate_create_request_normalises_a_good_request():
    out = logic.validate_create_request(_valid_create(prefs={"vibe": "  upbeat  ", "aspect": "9:16"}))
    assert out["content_type"] == "video/mp4"
    assert out["size"] == 10 * 1024 * 1024
    assert out["prefs"] == {"vibe": "upbeat", "aspect": "9:16"}


def test_validate_create_request_rejects_bad_content_type():
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(content_type="application/zip"))


def test_validate_create_request_enforces_size_cap():
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(size=logic.MAX_FILE_BYTES + 1))
    with pytest.raises(logic.ApiError):
        logic.validate_create_request(_valid_create(size=0))


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
        {"upload_id": "u1", "parts": [{"part_number": 2, "etag": "b"}, {"part_number": 1, "etag": "a"}]}
    )
    assert out["upload_id"] == "u1"
    assert out["parts"] == [{"PartNumber": 1, "ETag": "a"}, {"PartNumber": 2, "ETag": "b"}]


def test_validate_complete_request_rejects_bad_input():
    with pytest.raises(logic.ApiError):
        logic.validate_complete_request({"parts": [{"part_number": 1, "etag": "a"}]})
    with pytest.raises(logic.ApiError):
        logic.validate_complete_request({"upload_id": "u1", "parts": []})
    with pytest.raises(logic.ApiError):
        logic.validate_complete_request({"upload_id": "u1", "parts": [{"part_number": 0, "etag": "a"}]})
    with pytest.raises(logic.ApiError):
        logic.validate_complete_request({"upload_id": "u1", "parts": [{"part_number": 1}]})


def test_build_job_item_shapes_the_record():
    created = logic.now()
    request = logic.validate_create_request(_valid_create(prefs={"vibe": "calm"}))
    item = logic.build_job_item("abc", "source", "raw/abc/source", request, created, "user-1")
    assert item["pk"] == "JOB#abc"
    assert item["user_id"] == "user-1"
    assert item["status"] == "UPLOADING"
    assert item["created_at"] == created.isoformat()
    assert item["sources"]["source"] == {
        "key": "raw/abc/source",
        "kind": "video",
        "size": request["size"],
        "uploaded": False,
    }
    assert item["prefs"] == {"vibe": "calm"}
    assert item["ttl"] > int(created.timestamp())


# --- handler ----------------------------------------------------------------


def test_options_preflight_returns_204(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    resp = handler(_event("OPTIONS"))
    assert resp["statusCode"] == 204
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"


def test_create_job_writes_record_and_returns_presigned_url(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    resp = handler(_event("POST", body=_valid_create(prefs={"vibe": "punchy"})))

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["status"] == "UPLOADING"
    assert body["expires_in"] == logic.UPLOAD_URL_TTL
    job_id = body["job_id"]
    assert s3keys.raw_key(job_id, "source") in body["upload_url"]

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "UPLOADING"
    assert job.prefs == {"vibe": "punchy"}
    assert job.sources["source"].uploaded is False


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
    resp = handler(_event("POST", body=_valid_create(size=size)))

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["upload_type"] == "multipart"
    assert body["upload_id"]
    assert body["part_size"] == logic.PART_SIZE
    assert len(body["parts"]) == logic.part_count(size)
    assert [p["part_number"] for p in body["parts"]] == list(range(1, len(body["parts"]) + 1))
    assert "uploadId" in body["parts"][0]["url"]


def test_complete_upload_finalises_a_multipart_object(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    job_id = "job1"
    key = s3keys.raw_key(job_id, logic.SRC_ID)
    s3 = boto3.client("s3", region_name="us-east-1")
    boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"]).put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "user_id": "user-1",
            "status": "UPLOADING",
            "created_at": "2026-07-29T00:00:00+00:00",
        }
    )

    upload_id = s3.create_multipart_upload(Bucket=aws_stack["raw_bucket"], Key=key)["UploadId"]
    part = s3.upload_part(
        Bucket=aws_stack["raw_bucket"], Key=key, UploadId=upload_id, PartNumber=1, Body=b"payload"
    )

    resp = handler(
        _event(
            "POST",
            body={"upload_id": upload_id, "parts": [{"part_number": 1, "etag": part["ETag"]}]},
            job_id=job_id,
        )
    )
    assert resp["statusCode"] == 200

    head = s3.head_object(Bucket=aws_stack["raw_bucket"], Key=key)
    assert head["ContentLength"] == len(b"payload")


def test_create_job_rejects_invalid_body(aws_stack, monkeypatch):
    _set_api_env(monkeypatch, aws_stack)
    resp = handler(_event("POST", body={"content_type": "video/mp4"}))
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
