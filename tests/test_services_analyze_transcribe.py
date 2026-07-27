from __future__ import annotations

import json
import os
from pathlib import Path

import boto3
import pytest

from services.analyze_transcribe.handler import handler, run_analyze_transcribe
from services.common import dynamo, s3keys

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"

_CANNED_SEGMENTS = [
    {
        "start": 0.0,
        "end": 1.0,
        "text": "hello there",
        "avg_logprob": -0.2,
        "no_speech_prob": 0.05,
        "words": [
            {"start": 0.0, "end": 0.5, "word": "hello", "probability": 0.9},
            {"start": 0.5, "end": 1.0, "word": "there", "probability": 0.85},
        ],
    },
    {
        # hallucinated (both thresholds tripped) -- must never reach S3
        "start": 1.0,
        "end": 2.0,
        "text": "[unintelligible mumbling]",
        "avg_logprob": -1.5,
        "no_speech_prob": 0.9,
        "words": [{"start": 1.0, "end": 2.0, "word": "[unintelligible]", "probability": 0.1}],
    },
]


def _seed_job_with_fake_audio(aws_stack, job_id: str) -> str:
    audio_key = s3keys.work_audio_key(job_id, "src1")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(Bucket=aws_stack["work_bucket"], Key=audio_key, Body=b"not-real-flac-bytes")

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "ANALYZING",
            "analysis_keys": {"audio": {"src1": audio_key}},
        }
    )
    return audio_key


def test_run_analyze_transcribe_filters_hallucinations_and_flattens_words(aws_stack, monkeypatch):
    # run_transcription is monkeypatched -- proves handler wiring
    # (filter -> flatten -> upload -> nested dynamo write) without the real model
    job_id = "job1"
    _seed_job_with_fake_audio(aws_stack, job_id)

    import services.analyze_transcribe.handler as analyze_transcribe_handler

    monkeypatch.setattr(
        analyze_transcribe_handler,
        "run_transcription",
        lambda audio_path, model_dir: _CANNED_SEGMENTS,
    )

    run_analyze_transcribe(
        job_id,
        jobs_table=aws_stack["jobs_table"],
        work_bucket=aws_stack["work_bucket"],
        model_dir="/opt/whisper-model",
    )

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    transcript_key = job.analysis_keys["transcript"]["src1"]
    assert transcript_key == s3keys.work_transcript_key(job_id, "src1")

    s3 = boto3.client("s3", region_name="us-east-1")
    data = json.loads(s3.get_object(Bucket=aws_stack["work_bucket"], Key=transcript_key)["Body"].read())

    assert data["src_id"] == "src1"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["text"] == "hello there"
    assert data["words"] == [
        {"start": 0.0, "end": 0.5, "text": "hello"},
        {"start": 0.5, "end": 1.0, "text": "there"},
    ]


def test_run_analyze_transcribe_never_persists_hallucinated_segment_text(aws_stack, monkeypatch):
    job_id = "job1"
    _seed_job_with_fake_audio(aws_stack, job_id)

    import services.analyze_transcribe.handler as analyze_transcribe_handler

    monkeypatch.setattr(
        analyze_transcribe_handler,
        "run_transcription",
        lambda audio_path, model_dir: _CANNED_SEGMENTS,
    )

    run_analyze_transcribe(
        job_id,
        jobs_table=aws_stack["jobs_table"],
        work_bucket=aws_stack["work_bucket"],
        model_dir="/opt/whisper-model",
    )

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    transcript_key = job.analysis_keys["transcript"]["src1"]
    s3 = boto3.client("s3", region_name="us-east-1")
    raw_body = s3.get_object(Bucket=aws_stack["work_bucket"], Key=transcript_key)["Body"].read()

    assert b"unintelligible" not in raw_body


def test_handler_reads_env_vars_and_delegates_to_run_analyze_transcribe(aws_stack, monkeypatch):
    job_id = "job1"
    _seed_job_with_fake_audio(aws_stack, job_id)

    import services.analyze_transcribe.handler as analyze_transcribe_handler

    monkeypatch.setattr(
        analyze_transcribe_handler,
        "run_transcription",
        lambda audio_path, model_dir: _CANNED_SEGMENTS,
    )
    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])
    monkeypatch.setenv("MODEL_DIR", "/opt/whisper-model")

    assert handler({"job_id": job_id}) == {"job_id": job_id}
    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.analysis_keys["transcript"]["src1"] == s3keys.work_transcript_key(job_id, "src1")


@pytest.mark.whisper
@pytest.mark.media
def test_run_analyze_transcribe_real_whisper_model_round_trip(aws_stack):
    # deliberately excluded from default CI -- run manually with:
    #   WHISPER_MODEL_DIR=/opt/whisper-model pytest -m whisper
    pytest.importorskip("faster_whisper")
    model_dir = os.environ.get("WHISPER_MODEL_DIR")
    if not model_dir:
        pytest.skip("WHISPER_MODEL_DIR not set -- requires baked faster-whisper model weights")

    job_id = "job1"
    raw_key = s3keys.raw_key(job_id, "src1")
    clip_path = SAMPLE_DIR / "clip_a.mp4"
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(clip_path), aws_stack["raw_bucket"], raw_key)

    from services.probe.handler import run_probe

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "ANALYZING",
            "sources": {
                "src1": {"key": raw_key, "kind": "video", "size": clip_path.stat().st_size, "uploaded": True}
            },
        }
    )
    run_probe(
        job_id,
        jobs_table=aws_stack["jobs_table"],
        raw_bucket=aws_stack["raw_bucket"],
        work_bucket=aws_stack["work_bucket"],
    )

    run_analyze_transcribe(
        job_id,
        jobs_table=aws_stack["jobs_table"],
        work_bucket=aws_stack["work_bucket"],
        model_dir=model_dir,
    )

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert "transcript" in job.analysis_keys
