from __future__ import annotations

import os
import tempfile
from pathlib import Path

from renderer.probe import extract_audio_flac, probe_file, validate_probe
from services.common import dynamo, s3keys, storage
from services.common.logging import log_job
from services.common.tracing import segment


class ProbeRejected(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def run_probe(job_id: str, jobs_table: str, raw_bucket: str, work_bucket: str) -> dict:
    dynamo.start_step(jobs_table, job_id, "probe")
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

    dynamo.set_analysis_key(jobs_table, job_id, "audio", {src_id: audio_key})
    # no status write here -- ANALYZING already covers the whole Analyze
    # branch (Probe + the 3 parallel Analyze Lambdas) as of Phase 3.
    dynamo.finish_step(jobs_table, job_id, "probe")
    return {"subtitles_enabled": job.prefs.get("subtitles_enabled", True)}


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    with log_job(__name__, job_id), segment(__name__, job_id):
        result = run_probe(
            job_id,
            jobs_table=os.environ["JOBS_TABLE"],
            raw_bucket=os.environ["RAW_BUCKET"],
            work_bucket=os.environ["WORK_BUCKET"],
        )
    return {"job_id": job_id, **result}
