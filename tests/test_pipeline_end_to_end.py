from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

from services.common import dynamo, s3keys
from services.cut.handler import handler as cut_handler
from services.finish.handler import run_finish
from services.probe.handler import handler as probe_handler
from services.render.main import run_render_job
from services.trigger.handler import handler as trigger_handler

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"

STATE_MACHINE_DEFINITION = """
{
  "StartAt": "Done",
  "States": {"Done": {"Type": "Pass", "End": true}}
}
"""

# These e2e tests exercise the deterministic fallback path: Bedrock is not
# reachable under moto, so BedrockPlanner is stubbed to raise as if the service
# were unavailable, which is exactly the real trigger for the no-LLM fallback.
class _UnavailableBedrock:
    model_id = "amazon.nova-lite-v1:0"

    def __init__(self, *args, **kwargs):
        pass

    def generate(self, messages):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no bedrock"}}, "Converse")


# Phase 3: no more CANNED_PLAN -- the fallback planner is the only source of
# edit_plan in this pipeline now, driven entirely by real loudness data.
_CANNED_TRANSCRIBE_SEGMENTS = [
    {
        "start": 0.5,
        "end": 1.5,
        "text": "hello world",
        "avg_logprob": -0.2,
        "no_speech_prob": 0.05,
        "words": [
            {"start": 0.5, "end": 1.0, "word": "hello", "probability": 0.9},
            {"start": 1.0, "end": 1.5, "word": "world", "probability": 0.85},
        ],
    }
]


def _seed_uploading_job(aws_stack, job_id: str, prefs: dict) -> str:
    raw_key = s3keys.raw_key(job_id, "src1")
    clip_path = SAMPLE_DIR / "clip_a.mp4"

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(clip_path), aws_stack["raw_bucket"], raw_key)

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "UPLOADING",
            "prefs": dynamo.to_decimal(prefs),
            "sources": {
                "src1": {
                    "key": raw_key,
                    "kind": "video",
                    "size": clip_path.stat().st_size,
                    "uploaded": True,
                }
            },
            # deliberately no edit_plan seeded -- the whole point of Phase 3
            # is that the fallback planner produces it from real signals.
        }
    )
    return raw_key


def _start_execution_and_probe(aws_stack, monkeypatch, job_id: str, raw_key: str) -> dict:
    sfn = boto3.client("stepfunctions", region_name="us-east-1")
    state_machine = sfn.create_state_machine(
        name="montage-pipeline",
        definition=STATE_MACHINE_DEFINITION,
        roleArn="arn:aws:iam::123456789012:role/test-role",
    )

    monkeypatch.setenv("JOBS_TABLE", aws_stack["jobs_table"])
    monkeypatch.setenv("STATE_MACHINE_ARN", state_machine["stateMachineArn"])
    monkeypatch.setenv("RAW_BUCKET", aws_stack["raw_bucket"])
    monkeypatch.setenv("WORK_BUCKET", aws_stack["work_bucket"])
    monkeypatch.setenv("OUTPUT_BUCKET", aws_stack["output_bucket"])

    trigger_event = {
        "detail": {
            "bucket": {"name": aws_stack["raw_bucket"]},
            "object": {"key": raw_key, "etag": "etag1"},
        }
    }
    trigger_result = trigger_handler(trigger_event)
    assert trigger_result == {"job_id": job_id, "started": True}
    assert dynamo.get_job(aws_stack["jobs_table"], job_id).status == "ANALYZING"

    probe_result = probe_handler({"job_id": job_id})
    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "ANALYZING"  # Probe no longer flips status on success (Phase 3)
    return probe_result


def _run_cut_render_finish(aws_stack, job_id: str, rsa_sha1_signing_available: bool) -> None:
    plan = dynamo.get_job(aws_stack["jobs_table"], job_id).edit_plan

    for index in range(len(plan["clips"])):
        cut_result = cut_handler({"job_id": job_id, "clip_indices": [index]})
        assert cut_result["clips"][0]["index"] == index

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert len(job.cut_keys) == len(plan["clips"])

    render_result = run_render_job(
        job_id,
        jobs_table=aws_stack["jobs_table"],
        work_bucket=aws_stack["work_bucket"],
        output_bucket=aws_stack["output_bucket"],
    )
    assert render_result.output_key == s3keys.output_key(job_id)
    assert dynamo.get_job(aws_stack["jobs_table"], job_id).output_key == render_result.output_key

    if not rsa_sha1_signing_available:
        pytest.skip("this OpenSSL build's crypto policy disables RSA+SHA1 signing")

    signed_url = run_finish(
        job_id,
        jobs_table=aws_stack["jobs_table"],
        output_bucket=aws_stack["output_bucket"],
        cloudfront_domain="cdn.example.com",
        key_pair_id="KEYPAIRID",
        private_key_pem=_throwaway_keypair(),
    )
    assert "Signature=" in signed_url

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "DONE"

    output_head = s3.head_object(Bucket=aws_stack["output_bucket"], Key=job.output_key)
    assert output_head["ContentLength"] > 0
    thumb_head = s3.head_object(Bucket=aws_stack["output_bucket"], Key=job.thumbnail_key)
    assert thumb_head["ContentLength"] > 0


@pytest.mark.media
def test_full_pipeline_with_subtitles_enabled_produces_a_fallback_plan_and_a_transcript(
    aws_stack, monkeypatch, rsa_sha1_signing_available
):
    job_id = "job1"
    raw_key = _seed_uploading_job(
        aws_stack, job_id, prefs={"subtitles_enabled": True, "max_duration": 18.0}
    )
    probe_result = _start_execution_and_probe(aws_stack, monkeypatch, job_id, raw_key)
    assert probe_result == {"job_id": job_id, "subtitles_enabled": True}

    from services.analyze_loudness.handler import handler as analyze_loudness_handler
    from services.analyze_scenes.handler import handler as analyze_scenes_handler
    import services.analyze_transcribe.handler as analyze_transcribe_handler
    from services.plan.handler import handler as plan_handler

    # 2a/2b. loudness + scenes always run, real ffmpeg, real sample clip
    analyze_loudness_handler({"job_id": job_id})
    analyze_scenes_handler({"job_id": job_id})

    # 2c. transcribe: subtitles_enabled is True so this branch runs too --
    # run_transcription is monkeypatched to avoid needing a real Whisper
    # model (that round trip is covered separately by the whisper-marked test).
    monkeypatch.setattr(
        analyze_transcribe_handler,
        "run_transcription",
        lambda audio_path, model_dir: _CANNED_TRANSCRIBE_SEGMENTS,
    )
    monkeypatch.setenv("MODEL_DIR", "/opt/whisper-model")
    analyze_transcribe_handler.handler({"job_id": job_id})

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.analysis_keys["transcript"]["src1"] == s3keys.work_transcript_key(job_id, "src1")

    # 3. plan: Bedrock unavailable -> fallback planner turns the loudness curve
    # into a real edit_plan
    monkeypatch.setattr("services.plan.handler.BedrockPlanner", _UnavailableBedrock)
    plan_handler({"job_id": job_id})
    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "RENDERING"
    assert job.edit_plan["summary"] == "Fallback plan (no-LLM): top loudness peaks"
    assert all("loudness peak" in clip["reason"] for clip in job.edit_plan["clips"])

    # 4-5. cut -> render -> finish, unchanged from Phase 2
    _run_cut_render_finish(aws_stack, job_id, rsa_sha1_signing_available)


@pytest.mark.media
def test_full_pipeline_with_subtitles_disabled_never_invokes_transcribe(
    aws_stack, monkeypatch, rsa_sha1_signing_available
):
    job_id = "job1"
    raw_key = _seed_uploading_job(
        aws_stack, job_id, prefs={"subtitles_enabled": False, "max_duration": 18.0}
    )
    probe_result = _start_execution_and_probe(aws_stack, monkeypatch, job_id, raw_key)
    assert probe_result == {"job_id": job_id, "subtitles_enabled": False}

    from services.analyze_loudness.handler import handler as analyze_loudness_handler
    from services.analyze_scenes.handler import handler as analyze_scenes_handler
    from services.plan.handler import handler as plan_handler

    analyze_loudness_handler({"job_id": job_id})
    analyze_scenes_handler({"job_id": job_id})
    # analyze_transcribe is deliberately never called here -- this is the
    # Python-level proxy for the Step Functions TranscribeChoice's
    # SkipTranscribe Pass branch (the real Choice-state behavior is only
    # verifiable post-deploy).

    monkeypatch.setattr("services.plan.handler.BedrockPlanner", _UnavailableBedrock)
    plan_handler({"job_id": job_id})
    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "RENDERING"
    assert "transcript" not in job.analysis_keys

    _run_cut_render_finish(aws_stack, job_id, rsa_sha1_signing_available)


def _throwaway_keypair() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
