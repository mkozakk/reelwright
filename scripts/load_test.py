#!/usr/bin/env python3
# fires N concurrent jobs against real AWS to exercise the render-concurrency
# semaphore and surface anything that only breaks under load (docs/phases/phase-6.md).
from __future__ import annotations

import argparse
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts._pipeline_client import (  # noqa: E402
    SAMPLE_CLIP,
    poll_until_terminal,
    seed_and_upload,
    terraform_outputs,
)

TIMEOUT_SECONDS = 25 * 60

# explicit, not relying on Probe's absent-key default -- matches smoke_test.py
PREFS = {"subtitles_enabled": True}


def run_one(outputs: dict) -> dict:
    job_id = f"load-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    seed_and_upload(job_id, outputs, PREFS)
    try:
        job = poll_until_terminal(outputs["jobs_table"], job_id, TIMEOUT_SECONDS)
        status, error = job.status, job.error
    except TimeoutError:
        status, error = "TIMEOUT", None
    return {"job_id": job_id, "status": status, "seconds": time.monotonic() - started, "error": error}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--max-workers", type=int, default=8)
    args = parser.parse_args()

    if not SAMPLE_CLIP.exists():
        print(f"missing sample clip: {SAMPLE_CLIP}", file=sys.stderr)
        return 1

    outputs = terraform_outputs()
    print(f"firing {args.count} jobs, {args.max_workers} at a time")

    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        results = list(pool.map(lambda _: run_one(outputs), range(args.count)))

    for r in results:
        suffix = f" ({r['error']})" if r["error"] else ""
        print(f"{r['job_id']}: {r['status']} in {r['seconds']:.0f}s{suffix}")

    failed = [r for r in results if r["status"] != "DONE"]
    print(f"\n{len(results) - len(failed)}/{len(results)} succeeded")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
