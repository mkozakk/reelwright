from __future__ import annotations

import os
import tempfile
from pathlib import Path

from renderer.probe import extract_audio_flac, probe_file, validate_probe
from services.common import dynamo, s3keys, storage


class ProbeRejected(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def run_probe(job_id: str, jobs_table: str, raw_bucket: str, work_bucket: str) -> None:
    job = dynamo.get_job(jobs_table, job_id)
    src_id, source = next(iter(job.sources.items()))  # single source in Phase 2

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        local_in = storage.download(raw_bucket, source.key, tmp_dir)

        result = probe_file(local_in)
        errors = validate_probe(result, source.size)
        if errors:
            dynamo.mark_failed(jobs_table, job_id, "; ".join(errors))
            raise ProbeRejected(errors)  # never retried into Fargate

        audio_key = s3keys.work_audio_key(job_id, src_id)
        local_flac = extract_audio_flac(local_in, tmp_dir / f"{src_id}.flac")
        storage.upload(work_bucket, audio_key, local_flac)

    dynamo.update_job(
        jobs_table,
        job_id,
        analysis_keys={"audio": {src_id: audio_key}},
        status="RENDERING",
    )


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    run_probe(
        job_id,
        jobs_table=os.environ["JOBS_TABLE"],
        raw_bucket=os.environ["RAW_BUCKET"],
        work_bucket=os.environ["WORK_BUCKET"],
    )
    return {"job_id": job_id}
