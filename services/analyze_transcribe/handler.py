from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from renderer.transcribe import filter_segments, run_transcription, words_from_segments
from services.common import dynamo, s3keys, storage
from services.common.logging import log_job


def run_analyze_transcribe(job_id: str, jobs_table: str, work_bucket: str, model_dir: str) -> None:
    dynamo.start_step(jobs_table, job_id, "transcribe")
    job = dynamo.get_job(jobs_table, job_id)
    src_id, audio_key = next(iter(job.analysis_keys["audio"].items()))  # single source in Phase 3

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        local_audio = storage.download(work_bucket, audio_key, tmp_dir)

        segments = run_transcription(local_audio, model_dir)
        kept_segments = filter_segments(segments)  # hallucinated segments never reach S3

        artifact = {
            "src_id": src_id,
            "segments": [
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"],
                    "avg_logprob": segment["avg_logprob"],
                    "no_speech_prob": segment["no_speech_prob"],
                }
                for segment in kept_segments
            ],
            "words": words_from_segments(kept_segments),
        }

        out_path = tmp_dir / f"{src_id}_transcript.json"
        out_path.write_text(json.dumps(artifact))

        transcript_key = s3keys.work_transcript_key(job_id, src_id)
        storage.upload(work_bucket, transcript_key, out_path)

    dynamo.set_analysis_key(jobs_table, job_id, "transcript", {src_id: transcript_key})
    dynamo.finish_step(jobs_table, job_id, "transcribe")


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    with log_job(__name__, job_id):
        run_analyze_transcribe(
            job_id,
            jobs_table=os.environ["JOBS_TABLE"],
            work_bucket=os.environ["WORK_BUCKET"],
            model_dir=os.environ["MODEL_DIR"],
        )
    return {"job_id": job_id}
