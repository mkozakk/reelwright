#!/usr/bin/env python3
# uploads assets/sample/clip_a.mp4 against real AWS, waits for the pipeline,
# downloads the result. Reads bucket/table names from `terraform output -json`.
from __future__ import annotations

import sys
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.common import s3keys  # noqa: E402

from scripts._pipeline_client import (  # noqa: E402
    SAMPLE_CLIP,
    execution_result_url,
    poll_until_terminal,
    seed_and_upload,
    terraform_outputs,
)

TIMEOUT_SECONDS = 20 * 60

# explicit, not relying on Probe's absent-key default -- exercises the
# Transcribe branch deterministically rather than leaving it to the default
PREFS = {"subtitles_enabled": True}


def main() -> int:
    if not SAMPLE_CLIP.exists():
        print(f"missing sample clip: {SAMPLE_CLIP}", file=sys.stderr)
        return 1

    outputs = terraform_outputs()
    job_id = f"smoke-{uuid.uuid4().hex[:8]}"
    print(f"job_id={job_id}")

    raw_key = s3keys.raw_key(job_id, "src1")
    print(f"uploading {SAMPLE_CLIP.name} -> s3://{outputs['raw_bucket']}/{raw_key}")
    etag = seed_and_upload(job_id, outputs, PREFS)

    try:
        job = poll_until_terminal(
            outputs["jobs_table"], job_id, TIMEOUT_SECONDS, on_status=lambda s: print(f"status={s}")
        )
    except TimeoutError:
        print("timed out waiting for the pipeline", file=sys.stderr)
        return 1

    if job.status == "FAILED":
        print(f"job failed: {job.error}", file=sys.stderr)
        return 1

    print(f"output_key={job.output_key}")
    print(f"thumbnail_key={job.thumbnail_key}")
    # the fallback planner is a silent safety net -- DONE alone doesn't prove
    # Nova planned the edit, so surface whether the LLM path or the fallback ran
    print(f"planning={job.planning}")

    signed_url = execution_result_url(outputs["state_machine_arn"], job_id, etag, PREFS)
    print(f"signed_url={signed_url}")

    dest = REPO_ROOT / "out" / f"{job_id}.mp4"
    dest.parent.mkdir(exist_ok=True)
    urllib.request.urlretrieve(signed_url, dest)
    print(f"downloaded -> {dest} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
