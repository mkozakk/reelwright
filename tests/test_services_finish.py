import base64
import time
from pathlib import Path

import boto3
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from renderer.cli import main as render_main
from services.common import dynamo, s3keys
from services.finish import cloudfront_sign
from services.finish.handler import FinishVerificationError, run_finish

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"

pytestmark = pytest.mark.usefixtures("requires_rsa_sha1_signing")


def _throwaway_keypair() -> bytes:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def test_sign_url_round_trips_with_the_matching_public_key():
    pem = _throwaway_keypair()
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private_key = load_pem_private_key(pem, password=None)
    url = "https://cdn.example.com/output/job1/montage.mp4"
    expires_at = int(time.time()) + 900

    signed = cloudfront_sign.sign_url(url, "KEYPAIRID", pem, expires_at)

    assert signed.startswith(f"{url}?")
    assert f"Expires={expires_at}" in signed
    assert "Key-Pair-Id=KEYPAIRID" in signed

    query = signed.split("?", 1)[1]
    params = dict(part.split("=", 1) for part in query.split("&"))
    signature = base64.b64decode(
        params["Signature"].replace("-", "+").replace("_", "=").replace("~", "/")
    )
    policy = cloudfront_sign.build_canned_policy(url, expires_at)

    private_key.public_key().verify(signature, policy, padding.PKCS1v15(), hashes.SHA1())


@pytest.mark.media
def test_run_finish_verifies_thumbnails_and_signs_a_url(aws_stack, tmp_path, monkeypatch):
    import services.finish.handler as finish_handler

    published = []
    monkeypatch.setattr(finish_handler.events, "publish", lambda *a, **kw: published.append((a, kw)))

    job_id = "job1"
    local_out = tmp_path / "montage.mp4"
    exit_code = render_main(
        [
            "render",
            str(SAMPLE_DIR / "plan_basic.json"),
            str(local_out),
            "--source", f"src1={SAMPLE_DIR / 'clip_a.mp4'}",
        ]
    )
    assert exit_code == 0

    output_key = s3keys.output_key(job_id)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(local_out), aws_stack["output_bucket"], output_key)

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={"pk": dynamo.job_pk(job_id), "status": "RENDERING", "output_key": output_key}
    )

    signed_url = run_finish(
        job_id,
        jobs_table=aws_stack["jobs_table"],
        output_bucket=aws_stack["output_bucket"],
        cloudfront_domain="cdn.example.com",
        key_pair_id="KEYPAIRID",
        private_key_pem=_throwaway_keypair(),
    )

    assert "Signature=" in signed_url
    assert "Key-Pair-Id=KEYPAIRID" in signed_url

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "DONE"
    assert job.thumbnail_key == s3keys.thumbnail_key(job_id)

    head = s3.head_object(Bucket=aws_stack["output_bucket"], Key=job.thumbnail_key)
    assert head["ContentLength"] > 0

    [(args, kwargs)] = published
    assert args == ("job.rendered", job_id)
    assert kwargs["status"] == "DONE"


@pytest.mark.media
def test_run_finish_sends_a_completion_email_when_notify_email_is_set(aws_stack, tmp_path, monkeypatch):
    import services.finish.handler as finish_handler

    sent = []
    monkeypatch.setattr(
        finish_handler.notify, "send_completion_email", lambda *a, **kw: sent.append((a, kw))
    )

    job_id = "job1"
    local_out = tmp_path / "montage.mp4"
    render_main(
        [
            "render",
            str(SAMPLE_DIR / "plan_basic.json"),
            str(local_out),
            "--source", f"src1={SAMPLE_DIR / 'clip_a.mp4'}",
        ]
    )
    output_key = s3keys.output_key(job_id)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(local_out), aws_stack["output_bucket"], output_key)

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={
            "pk": dynamo.job_pk(job_id),
            "status": "RENDERING",
            "output_key": output_key,
            "notify_email": "watcher@example.com",
        }
    )

    signed_url = run_finish(
        job_id,
        jobs_table=aws_stack["jobs_table"],
        output_bucket=aws_stack["output_bucket"],
        cloudfront_domain="cdn.example.com",
        key_pair_id="KEYPAIRID",
        private_key_pem=_throwaway_keypair(),
        ses_from_email="noreply@example.com",
    )

    [(args, kwargs)] = sent
    assert args == ("noreply@example.com", "watcher@example.com", signed_url)


@pytest.mark.media
def test_run_finish_rejects_zero_duration_output(aws_stack, tmp_path, monkeypatch):
    job_id = "job1"
    local_out = tmp_path / "montage.mp4"
    render_main(
        [
            "render",
            str(SAMPLE_DIR / "plan_basic.json"),
            str(local_out),
            "--source", f"src1={SAMPLE_DIR / 'clip_a.mp4'}",
        ]
    )
    output_key = s3keys.output_key(job_id)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.upload_file(str(local_out), aws_stack["output_bucket"], output_key)

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(aws_stack["jobs_table"])
    table.put_item(
        Item={"pk": dynamo.job_pk(job_id), "status": "RENDERING", "output_key": output_key}
    )

    import services.finish.handler as finish_handler

    def fake_probe_file(path):
        from renderer.probe import ProbeResult

        return ProbeResult(duration=0.0, width=0, height=0, fps=0.0, video_codec="h264", audio_codec=None)

    monkeypatch.setattr(finish_handler, "probe_file", fake_probe_file)

    with pytest.raises(FinishVerificationError):
        run_finish(
            job_id,
            jobs_table=aws_stack["jobs_table"],
            output_bucket=aws_stack["output_bucket"],
            cloudfront_domain="cdn.example.com",
            key_pair_id="KEYPAIRID",
            private_key_pem=_throwaway_keypair(),
        )

    job = dynamo.get_job(aws_stack["jobs_table"], job_id)
    assert job.status == "FAILED"
