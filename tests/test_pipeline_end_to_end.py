from pathlib import Path

import boto3
import pytest

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

# Phase 2's canned plan: single source (only one file is uploaded per job
# until v1.2 sessions), a crossfade so Cut/Render's segment boundary is
# actually exercised, subtitles off (see docs/phases/phase-2.md -- retiming
# subtitles across cut segments isn't built until real transcripts land).
CANNED_PLAN = {
    "version": "1",
    "summary": "Phase 2 canned plan",
    "clips": [
        {
            "source": "src1",
            "start": 0.5,
            "end": 3.5,
            "reason": "opening beat",
            "transition_out": {"type": "crossfade", "duration": 0.5},
        },
        {"source": "src1", "start": 4.0, "end": 7.0, "reason": "closing beat"},
    ],
    "subtitles": {"enabled": False},
    "color": {"preset": "cinematic", "adjust": {"contrast": 1.05, "saturation": 1.1}},
    "audio": {"music_track": None, "duck_under_speech": False},
    "output": {"aspect": "16:9", "resolution": "720p", "max_duration": 15},
}


@pytest.mark.media
def test_full_pipeline_trigger_probe_cut_render_finish(aws_stack, monkeypatch, rsa_sha1_signing_available):
    job_id = "job1"
    raw_key = s3keys.raw_key(job_id, "src1")
    clip_path = SAMPLE_DIR / "clip_a.mp4"

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(clip_path), aws_stack["raw_bucket"], raw_key)

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "UPLOADING",
            "prefs": {},
            "sources": {
                "src1": {
                    "key": raw_key,
                    "kind": "video",
                    "size": clip_path.stat().st_size,
                    "uploaded": True,
                }
            },
            "edit_plan": dynamo.to_decimal(CANNED_PLAN),
        }
    )

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

    # 1. trigger: S3 upload -> conditional status flip -> StartExecution
    trigger_event = {
        "detail": {
            "bucket": {"name": aws_stack["raw_bucket"]},
            "object": {"key": raw_key, "etag": "etag1"},
        }
    }
    trigger_result = trigger_handler(trigger_event)
    assert trigger_result == {"job_id": job_id, "started": True}
    assert dynamo.get_job(aws_stack["jobs_table"], job_id).status == "ANALYZING"

    # 2. probe: validate + extract audio
    probe_handler({"job_id": job_id})
    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "RENDERING"
    assert job.analysis_keys["audio"]["src1"] == s3keys.work_audio_key(job_id, "src1")

    # 3. cut: one Map-state invocation per clip, exactly as Step Functions would fan out
    for index in range(len(CANNED_PLAN["clips"])):
        cut_result = cut_handler({"job_id": job_id, "clip_indices": [index]})
        assert cut_result["clips"][0]["index"] == index

    listing = s3.list_objects_v2(
        Bucket=aws_stack["work_bucket"], Prefix=s3keys.work_clips_prefix(job_id)
    )
    assert listing["KeyCount"] == len(CANNED_PLAN["clips"])

    # 4. render: concat the cut segments with the crossfade + color grade
    render_result = run_render_job(
        job_id,
        jobs_table=aws_stack["jobs_table"],
        work_bucket=aws_stack["work_bucket"],
        output_bucket=aws_stack["output_bucket"],
    )
    assert render_result.output_key == s3keys.output_key(job_id)
    assert dynamo.get_job(aws_stack["jobs_table"], job_id).output_key == render_result.output_key

    # 5. finish: verify, thumbnail, sign, DONE -- steps 1-4 above are fully
    # verified regardless; only this last step needs real RSA+SHA1 signing.
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


def _throwaway_keypair() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
