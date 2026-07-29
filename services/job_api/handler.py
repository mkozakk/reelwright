from __future__ import annotations

import json
import os
import time

import boto3

from services.common import dynamo, s3keys
from services.common.logging import get_logger

from ..finish import cloudfront_sign
from . import logic

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _respond(status: int, body: dict | None) -> dict:
    out = {"statusCode": status, "headers": dict(CORS_HEADERS)}
    if body is not None:
        out["headers"]["Content-Type"] = "application/json"
        out["body"] = json.dumps(body)
    return out


def _presign_upload(key: str, request: dict) -> dict:
    """Single presigned PUT for small files; a multipart upload with one
    presigned URL per part for large ones (docs/phases/phase-5.md). The browser
    completes a multipart upload via POST /jobs/{id}/complete."""
    s3 = boto3.client("s3")
    bucket = os.environ["RAW_BUCKET"]
    content_type = request["content_type"]

    if not logic.wants_multipart(request["size"]):
        url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": request["size"],
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
        for n in range(1, logic.part_count(request["size"]) + 1)
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
    request = logic.validate_create_request(logic.parse_body(event.get("body")))

    created = logic.now()
    ip = logic.client_ip(event)
    if not dynamo.claim_ip_slot(
        jobs_table, ip, logic.ip_cap_day(created), logic.IP_DAILY_CAP, logic.ip_cap_ttl(created)
    ):
        raise logic.ApiError(429, "daily job limit reached for this address")

    job_id = logic.new_job_id()
    key = s3keys.raw_key(job_id, logic.SRC_ID)
    dynamo.put_new_job(
        jobs_table, logic.build_job_item(job_id, logic.SRC_ID, key, request, created)
    )
    get_logger(__name__, job_id).info("job created")

    body = {"job_id": job_id, "status": "UPLOADING"}
    body.update(_presign_upload(key, request))
    return _respond(201, body)


def _complete_upload(event: dict, job_id: str) -> dict:
    request = logic.validate_complete_request(logic.parse_body(event.get("body")))
    boto3.client("s3").complete_multipart_upload(
        Bucket=os.environ["RAW_BUCKET"],
        Key=s3keys.raw_key(job_id, logic.SRC_ID),
        UploadId=request["upload_id"],
        MultipartUpload={"Parts": request["parts"]},
    )
    return _respond(200, {"job_id": job_id, "status": "UPLOADING"})


def _sign(key: str) -> str:
    url = f"https://{os.environ['CLOUDFRONT_DOMAIN']}/{key}"
    return cloudfront_sign.sign_url(
        url,
        os.environ["CLOUDFRONT_KEY_PAIR_ID"],
        os.environ["CLOUDFRONT_PRIVATE_KEY_PEM"].encode(),
        expires_at=int(time.time()) + logic.PLAYBACK_URL_TTL,
    )


def _get_job(job_id: str) -> dict:
    try:
        job = dynamo.get_job(os.environ["JOBS_TABLE"], job_id)
    except KeyError:
        raise logic.ApiError(404, "job not found")

    body = logic.status_response(job)
    if job.status == "DONE" and job.output_key:
        body["output_url"] = _sign(job.output_key)
        if job.thumbnail_key:
            body["thumbnail_url"] = _sign(job.thumbnail_key)
        body["expires_in"] = logic.PLAYBACK_URL_TTL
    return _respond(200, body)


def handler(event: dict, context=None) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "")
    job_id = (event.get("pathParameters") or {}).get("id")

    if method == "OPTIONS":
        return _respond(204, None)
    try:
        if method == "POST" and not job_id:
            return _create_job(event)
        if method == "POST" and job_id:
            return _complete_upload(event, job_id)
        if method == "GET" and job_id:
            return _get_job(job_id)
        raise logic.ApiError(404, "no such route")
    except logic.ApiError as exc:
        get_logger(__name__, job_id).info("rejected: %s", exc.message)
        return _respond(exc.status, {"error": exc.message})
