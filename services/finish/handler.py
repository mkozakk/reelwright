from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from renderer.probe import probe_file
from renderer.thumbnail import extract_thumbnail
from services.common import dynamo, s3keys, storage
from services.common.logging import log_job

from . import cloudfront_sign

SIGNED_URL_TTL_SECONDS = 900  # 15 min, per docs.DESIGN.md output/download URLs


class FinishVerificationError(RuntimeError):
    pass


def run_finish(
    job_id: str,
    jobs_table: str,
    output_bucket: str,
    cloudfront_domain: str,
    key_pair_id: str,
    private_key_pem: bytes,
) -> str:
    dynamo.start_step(jobs_table, job_id, "finish")
    job = dynamo.get_job(jobs_table, job_id)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        local_out = storage.download(output_bucket, job.output_key, tmp_dir)

        result = probe_file(local_out)
        if result.duration <= 0:
            dynamo.mark_failed(jobs_table, job_id, "output failed verification")
            raise FinishVerificationError(f"output for '{job_id}' has zero duration")

        thumb_path = extract_thumbnail(local_out, tmp_dir / "thumbnail.jpg")
        thumb_key = s3keys.thumbnail_key(job_id)
        storage.upload(output_bucket, thumb_key, thumb_path)

    url = f"https://{cloudfront_domain}/{job.output_key}"
    signed_url = cloudfront_sign.sign_url(
        url, key_pair_id, private_key_pem, expires_at=int(time.time()) + SIGNED_URL_TTL_SECONDS
    )

    dynamo.update_job(jobs_table, job_id, status="DONE", thumbnail_key=thumb_key)
    dynamo.finish_step(jobs_table, job_id, "finish")
    return signed_url


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    with log_job(__name__, job_id):
        signed_url = run_finish(
            job_id,
            jobs_table=os.environ["JOBS_TABLE"],
            output_bucket=os.environ["OUTPUT_BUCKET"],
            cloudfront_domain=os.environ["CLOUDFRONT_DOMAIN"],
            key_pair_id=os.environ["CLOUDFRONT_KEY_PAIR_ID"],
            private_key_pem=os.environ["CLOUDFRONT_PRIVATE_KEY_PEM"].encode(),
        )
    return {"job_id": job_id, "status": "DONE", "url": signed_url}
