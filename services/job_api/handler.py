from __future__ import annotations

import json
import os
import time

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from renderer.edit_plan.models import EditPlan
from renderer.edit_plan.validate import EditPlanValidationError, SourceBounds, validate_plan
from services.common import dynamo, events, s3keys
from services.common.logging import get_logger
from services.common.tracing import segment

from ..finish import cloudfront_sign
from . import logic

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
}


def _respond(status: int, body: dict | None) -> dict:
    out = {"statusCode": status, "headers": dict(CORS_HEADERS)}
    if body is not None:
        out["headers"]["Content-Type"] = "application/json"
        out["body"] = json.dumps(body)
    return out


def _presign_upload(key: str, content_type: str, size: int) -> dict:
    """Single presigned PUT for small files; a multipart upload with one
    presigned URL per part for large ones (docs/phases/phase-5.md). The browser
    completes a multipart upload via POST /jobs/{id}/complete."""
    s3 = boto3.client("s3")
    bucket = os.environ["RAW_BUCKET"]

    if not logic.wants_multipart(size):
        url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": size,
            },
            ExpiresIn=logic.UPLOAD_URL_TTL,
        )
        return {
            "upload_type": "single",
            "upload_url": url,
            "upload_method": "PUT",
            "upload_headers": {"Content-Type": content_type},
            "expires_in": logic.UPLOAD_URL_TTL,
        }

    upload_id = s3.create_multipart_upload(
        Bucket=bucket, Key=key, ContentType=content_type
    )["UploadId"]
    parts = [
        {
            "part_number": n,
            "url": s3.generate_presigned_url(
                "upload_part",
                Params={"Bucket": bucket, "Key": key, "UploadId": upload_id, "PartNumber": n},
                ExpiresIn=logic.UPLOAD_URL_TTL,
            ),
        }
        for n in range(1, logic.part_count(size) + 1)
    ]
    return {
        "upload_type": "multipart",
        "upload_id": upload_id,
        "part_size": logic.PART_SIZE,
        "parts": parts,
        "expires_in": logic.UPLOAD_URL_TTL,
    }


def _create_job(event: dict) -> dict:
    jobs_table = os.environ["JOBS_TABLE"]
    user_id = logic.user_id_from_claims(event)
    request = logic.validate_create_request(logic.parse_body(event.get("body")))

    created = logic.now()
    ip = logic.client_ip(event)
    day = logic.cap_day(created)
    ttl = logic.cap_ttl(created)
    if not dynamo.claim_ip_slot(jobs_table, ip, day, logic.IP_DAILY_CAP, ttl):
        raise logic.ApiError(429, "daily job limit reached for this address")
    if not dynamo.claim_user_slot(jobs_table, user_id, day, logic.USER_DAILY_CAP, ttl):
        raise logic.ApiError(429, "daily job limit reached for this account")

    job_id = logic.new_job_id()
    with segment(__name__, job_id):
        item = logic.build_job_item(job_id, request["files"], request, created, user_id)
        dynamo.put_new_job(jobs_table, item)
        events.publish("job.created", job_id, user_id=user_id, status="UPLOADING")
        get_logger(__name__, job_id).info("job created")

        sources = [
            {
                "src_id": src_id,
                "kind": item["sources"][src_id]["kind"],
                **_presign_upload(item["sources"][src_id]["key"], f["content_type"], f["size"]),
            }
            for src_id, f in zip(logic.src_ids(len(request["files"])), request["files"])
        ]
        body = {"job_id": job_id, "status": "UPLOADING", "sources": sources}
    return _respond(201, body)


def _owned_job(event: dict, job_id: str):
    try:
        job = dynamo.get_job(os.environ["JOBS_TABLE"], job_id)
    except KeyError:
        raise logic.ApiError(404, "job not found")
    if job.user_id != logic.user_id_from_claims(event):
        # 404, not 403 -- don't confirm existence to a non-owner (capability-URL
        # philosophy carried over from the pre-auth model, docs/phases/phase-5.md)
        raise logic.ApiError(404, "job not found")
    return job


def _complete_upload(event: dict, job_id: str) -> dict:
    with segment(__name__, job_id):
        job = _owned_job(event, job_id)
        request = logic.validate_complete_request(logic.parse_body(event.get("body")))
        src_id = request["src_id"]
        if src_id not in job.sources:
            raise logic.ApiError(400, f"unknown src_id '{src_id}'")
        boto3.client("s3").complete_multipart_upload(
            Bucket=os.environ["RAW_BUCKET"],
            Key=job.sources[src_id].key,
            UploadId=request["upload_id"],
            MultipartUpload={"Parts": request["parts"]},
        )
    return _respond(200, {"job_id": job_id, "status": "UPLOADING"})


def _start_job(event: dict, job_id: str) -> dict:
    with segment(__name__, job_id):
        job = _owned_job(event, job_id)
        jobs_table = os.environ["JOBS_TABLE"]

        if job.status != "UPLOADING":
            # idempotent no-op -- covers a double /start, and a /start that
            # arrives after the pipeline already moved on
            return _respond(200, {"job_id": job_id, "status": job.status})

        s3 = boto3.client("s3")
        raw_bucket = os.environ["RAW_BUCKET"]
        missing: list[str] = []
        etags: dict[str, str] = {}
        for src_id, source in job.sources.items():
            try:
                head = s3.head_object(Bucket=raw_bucket, Key=source.key)
            except ClientError:
                missing.append(src_id)
                continue
            if head["ContentLength"] != source.size:
                missing.append(src_id)
                continue
            etags[src_id] = head["ETag"].strip('"')

        if missing:
            # client error, not a job failure -- the job stays in UPLOADING
            # so the client can finish uploading and retry /start
            return _respond(
                409,
                {"error": "upload(s) incomplete or mismatched", "missing_or_mismatched": sorted(missing)},
            )

        for src_id in job.sources:
            dynamo.update_source(jobs_table, job_id, src_id, uploaded=True)

        flipped = dynamo.conditional_status_flip(jobs_table, job_id, "UPLOADING", "ANALYZING")
        if not flipped:
            # lost a race to a concurrent /start -- same idempotent no-op
            current = dynamo.get_job(jobs_table, job_id)
            return _respond(200, {"job_id": job_id, "status": current.status})

        source_items = [{"job_id": job_id, "src_id": sid} for sid in sorted(job.sources)]
        name = logic.start_execution_name(job_id, etags, logic.hash_prefs(job.prefs))
        sfn = boto3.client("stepfunctions")
        try:
            sfn.start_execution(
                stateMachineArn=os.environ["STATE_MACHINE_ARN"],
                name=name,
                input=json.dumps({"job_id": job_id, "mode": "new", "source_items": source_items}),
            )
        except sfn.exceptions.ExecutionAlreadyExists:
            pass

        get_logger(__name__, job_id).info("session started")
    return _respond(202, {"job_id": job_id, "status": "ANALYZING"})


def _sign(key: str) -> str:
    url = f"https://{os.environ['CLOUDFRONT_DOMAIN']}/{key}"
    return cloudfront_sign.sign_url(
        url,
        os.environ["CLOUDFRONT_KEY_PAIR_ID"],
        os.environ["CLOUDFRONT_PRIVATE_KEY_PEM"].encode(),
        expires_at=int(time.time()) + logic.PLAYBACK_URL_TTL,
    )


def _get_job(event: dict, job_id: str) -> dict:
    with segment(__name__, job_id):
        job = _owned_job(event, job_id)

        body = logic.status_response(job)
        if job.status == "DONE" and job.output_key:
            body["output_url"] = _sign(job.output_key)
            if job.thumbnail_key:
                body["thumbnail_url"] = _sign(job.thumbnail_key)
            body["expires_in"] = logic.PLAYBACK_URL_TTL
    return _respond(200, body)


def _rerender(event: dict, job_id: str) -> dict:
    with segment(__name__, job_id):
        job = _owned_job(event, job_id)
        jobs_table = os.environ["JOBS_TABLE"]

        body = logic.parse_body(event.get("body"))
        sources = {
            sid: SourceBounds(kind=s.kind, duration=s.duration or 0.0) for sid, s in job.sources.items()
        }
        try:
            plan = validate_plan(EditPlan.model_validate(body), job.prefs, sources=sources)
        except (ValidationError, EditPlanValidationError) as exc:
            raise logic.ApiError(400, f"invalid edit plan: {exc}")

        created = logic.now()
        if not dynamo.claim_user_slot(
            jobs_table, job.user_id, logic.cap_day(created), logic.USER_DAILY_CAP, logic.cap_ttl(created)
        ):
            # a re-render is a render -- it counts against the same daily quota
            raise logic.ApiError(429, "daily job limit reached for this account")

        edit_plan_json = plan.model_dump_json()
        dynamo.update_job(jobs_table, job_id, edit_plan=json.loads(edit_plan_json), status="RENDERING")

        sfn = boto3.client("stepfunctions")
        try:
            sfn.start_execution(
                stateMachineArn=os.environ["STATE_MACHINE_ARN"],
                name=logic.rerender_execution_name(job_id, edit_plan_json),
                input=json.dumps({"job_id": job_id, "mode": "rerender"}),
            )
        except sfn.exceptions.ExecutionAlreadyExists:
            pass

        get_logger(__name__, job_id).info("rerender started")
    return _respond(202, {"job_id": job_id, "status": "RENDERING"})


def _list_jobs(event: dict) -> dict:
    with segment(__name__, None):
        jobs = dynamo.list_jobs_for_user(os.environ["JOBS_TABLE"], logic.user_id_from_claims(event))
    return _respond(200, {"jobs": jobs})


def handler(event: dict, context=None) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "")
    path = http.get("path", "")
    job_id = (event.get("pathParameters") or {}).get("id")

    if method == "OPTIONS":
        return _respond(204, None)
    try:
        if method == "POST" and not job_id:
            return _create_job(event)
        if method == "POST" and job_id and path.endswith("/rerender"):
            return _rerender(event, job_id)
        if method == "POST" and job_id and path.endswith("/start"):
            return _start_job(event, job_id)
        if method == "POST" and job_id:
            return _complete_upload(event, job_id)
        if method == "GET" and job_id:
            return _get_job(event, job_id)
        if method == "GET" and not job_id:
            return _list_jobs(event)
        raise logic.ApiError(404, "no such route")
    except logic.ApiError as exc:
        get_logger(__name__, job_id).info("rejected: %s", exc.message)
        return _respond(exc.status, {"error": exc.message})
