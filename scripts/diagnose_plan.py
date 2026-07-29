#!/usr/bin/env python3
# Why did a job fall back? Pulls that job's real analysis evidence from the work
# bucket and runs the planner against it, printing each attempt's validation
# errors -- the reason a plan is rejected is not otherwise recorded (logs keep
# evidence contents out, docs/DESIGN.md §10 layer 7).
#
#   python scripts/diagnose_plan.py <job_id> [bedrock_region] [guardrail_id]
#
# Runs WITHOUT the guardrail by default so a failure isolates to schema/structural
# validation; pass the guardrail id as a third arg to test the guardrail-blocked
# hypothesis instead.
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.common import dynamo  # noqa: E402
from services.plan import evidence as evidence_mod  # noqa: E402
from services.plan import prompt  # noqa: E402
from services.plan.bedrock_planner import BedrockPlanner  # noqa: E402
from services.plan.handler import (  # noqa: E402
    _drop_micro_clips,
    _remap_unknown_sources,
    _single_source,
    _try_validate,
)

TF_DIR = REPO_ROOT / "infra" / "envs" / "dev"


def tf_outputs() -> dict:
    result = subprocess.run(
        ["terraform", "output", "-json"], cwd=TF_DIR, capture_output=True, text=True, check=True
    )
    return {k: v["value"] for k, v in json.loads(result.stdout).items()}


def _load(s3, bucket: str, entry: dict | None, src_id: str) -> dict | None:
    if not entry or src_id not in entry:
        return None
    return json.loads(s3.get_object(Bucket=bucket, Key=entry[src_id])["Body"].read())


def main() -> int:
    job_id = sys.argv[1]
    region = sys.argv[2] if len(sys.argv) > 2 else "us-east-1"
    guardrail_id = sys.argv[3] if len(sys.argv) > 3 else None

    outputs = tf_outputs()
    job = dynamo.get_job(outputs["jobs_table"], job_id)
    s3 = boto3.client("s3")
    work_bucket = outputs["work_bucket"]
    src_id = _single_source(job.analysis_keys)
    known_sources = list(job.sources) or [src_id]

    loudness = _load(s3, work_bucket, job.analysis_keys.get("loudness"), src_id)
    scenes = _load(s3, work_bucket, job.analysis_keys.get("scenes"), src_id)
    transcript = _load(s3, work_bucket, job.analysis_keys.get("transcript"), src_id)

    ev = evidence_mod.build_evidence(loudness, scenes, transcript, source_ids=known_sources)
    print("prefs:", job.prefs)
    print("evidence sizes:", {
        "loudness_points": len(ev["loudness_points"]),
        "scene_cuts": len(ev["scene_cuts"]),
        "phrases": len(ev["phrases"]),
        "source_duration": ev["source_duration"],
    })
    print("guardrail:", guardrail_id or "off")

    prompt_prefs = evidence_mod.prefs_for_prompt(job.prefs)
    planner = BedrockPlanner(region=region, guardrail_id=guardrail_id)
    messages = prompt.build_messages(ev, prompt_prefs)

    for attempt in (0, 1):
        raw, usage = planner.generate(messages)
        raw = _remap_unknown_sources(raw, known_sources)
        raw = _drop_micro_clips(raw)
        plan, errors = _try_validate(raw, job.prefs)
        print(f"\nattempt {attempt}: usage={usage}, tool_output={'yes' if raw else 'NONE (guardrail block?)'}")
        if plan is not None:
            print(f"  VALID -- {len(plan.clips)} clips, summary: {plan.summary[:80]!r}")
            return 0
        print("  REJECTED:")
        for e in errors:
            print("   -", e)
        if raw:
            spans = [(c.get("start"), c.get("end")) for c in raw.get("clips", [])]
            print(f"  {len(spans)} clip spans (start,end): {spans[:12]}")
        messages = prompt.retry_messages(ev, prompt_prefs, raw or {}, errors)

    print("\n-> both attempts failed -> job falls back to the no-LLM planner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
