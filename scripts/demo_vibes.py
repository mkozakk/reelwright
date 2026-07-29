#!/usr/bin/env python3
# Phase 4 demo (v0.4 acceptance): same footage, two vibes -> two different
# montage plans. Pulls a job's real analysis evidence from the work bucket and
# runs the planner for each vibe, printing the resulting plan. Runs the planner
# locally against real Bedrock -- no redeploy needed.
#
#   python scripts/demo_vibes.py <job_id> [bedrock_region]
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from renderer.presets import music as music_presets  # noqa: E402
from services.common import dynamo  # noqa: E402
from services.plan import evidence as evidence_mod  # noqa: E402
from services.plan.bedrock_planner import BedrockPlanner  # noqa: E402
from services.plan.handler import _plan_with_llm, _single_source  # noqa: E402

TF_DIR = REPO_ROOT / "infra" / "envs" / "dev"
VIBES = ["energetic", "cinematic"]


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

    outputs = tf_outputs()
    job = dynamo.get_job(outputs["jobs_table"], job_id)
    s3 = boto3.client("s3")
    work_bucket = outputs["work_bucket"]
    src_id = _single_source(job.analysis_keys)
    known_sources = list(job.sources) or [src_id]

    loudness = _load(s3, work_bucket, job.analysis_keys.get("loudness"), src_id)
    scenes = _load(s3, work_bucket, job.analysis_keys.get("scenes"), src_id)
    transcript = _load(s3, work_bucket, job.analysis_keys.get("transcript"), src_id)

    manifest = music_presets.load_manifest()
    catalog = [{"id": key, "mood": entry.get("mood", "")} for key, entry in manifest.items()]
    evidence = evidence_mod.build_evidence(
        loudness, scenes, transcript, source_ids=known_sources, music_tracks=catalog
    )
    print(f"evidence: {len(evidence['scene_cuts'])} scenes, {len(evidence['phrases'])} phrases, "
          f"{evidence['source_duration']}s source\n")

    planner = BedrockPlanner(region=region)
    for vibe in VIBES:
        prefs = {"vibe": vibe, "max_duration": 30.0, "subtitles_enabled": False}
        prompt_prefs = evidence_mod.prefs_for_prompt(prefs)
        plan, meta = _plan_with_llm(planner, evidence, prompt_prefs, prefs, known_sources, set(manifest))

        print(f"=== vibe: {vibe} ===  (source={meta['source']}, attempts={meta['attempts']})")
        if plan is None:
            print("  fell back to the no-LLM planner\n")
            continue
        print(f"  summary : {plan.summary[:90]}")
        print(f"  color   : {plan.color.preset}    music: {plan.audio.music_track}")
        for i, clip in enumerate(plan.clips):
            transition = clip.transition_out.type if clip.transition_out else "cut"
            print(f"  clip {i}: {clip.start:.1f}-{clip.end:.1f}s speed={clip.speed} "
                  f"[{transition}] {clip.reason[:50]}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
