from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from renderer.edit_plan.models import EditPlan
from renderer.edit_plan.validate import (
    DURATION_EPSILON,
    MIN_CLIP_SECONDS,
    EditPlanValidationError,
    SourceBounds,
    validate_plan,
)
from renderer.presets import music as music_presets
from services.common import dynamo, events, storage
from services.common.logging import get_logger, log_job
from services.common.tracing import segment

from . import evidence as evidence_mod
from . import fallback_planner, prompt
from .bedrock_planner import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL_ID,
    BedrockPlanner,
)

NOVA_PRICE_PER_MTOK = {"nova-lite": (0.06, 0.24)}


def run_plan(
    job_id: str,
    jobs_table: str,
    work_bucket: str,
    planner: BedrockPlanner | None = None,
    model_id: str = DEFAULT_MODEL_ID,
    region: str | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    guardrail_id: str | None = None,
    guardrail_version: str | None = None,
) -> None:
    dynamo.update_job(jobs_table, job_id, status="PLANNING")
    dynamo.start_step(jobs_table, job_id, "plan")
    log = get_logger(__name__, job_id)

    job = dynamo.get_job(jobs_table, job_id)
    # only sources Analyze actually touched -- audio-only assets never enter
    # Analyze (docs/phases/phase-8.md), so analysis_keys is the source of
    # truth for "which sources have editorial evidence", not job.sources
    video_sources = sorted(job.analysis_keys.get("loudness", {}))
    audio_assets = {sid: s for sid, s in job.sources.items() if s.kind == "audio"}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        per_source = {
            sid: {
                "loudness": _load(job.analysis_keys, "loudness", sid, work_bucket, tmp_dir),
                "scenes": _load(job.analysis_keys, "scenes", sid, work_bucket, tmp_dir),
                "transcript": _load(job.analysis_keys, "transcript", sid, work_bucket, tmp_dir),
            }
            for sid in video_sources
        }

    manifest = music_presets.load_manifest()
    music_catalog = [{"id": key, "mood": entry.get("mood", "")} for key, entry in manifest.items()]
    music_catalog += [{"id": f"user:{sid}", "mood": "user-uploaded"} for sid in audio_assets]

    evidence = evidence_mod.build_evidence(per_source, music_catalog)
    prompt_prefs = evidence_mod.prefs_for_prompt(job.prefs)

    sources_for_validation = {
        sid: SourceBounds(kind=s.kind, duration=s.duration or 0.0) for sid, s in job.sources.items()
    }
    valid_tracks = set(manifest) | {f"user:{sid}" for sid in audio_assets}

    planner = planner or BedrockPlanner(
        model_id,
        region,
        max_output_tokens,
        guardrail_id=guardrail_id,
        guardrail_version=guardrail_version,
    )
    plan, meta = _plan_with_llm(
        planner, evidence, prompt_prefs, job.prefs, video_sources, valid_tracks, sources_for_validation, log
    )

    if plan is None:
        raw = fallback_planner.build_plan(
            {
                sid: {
                    "points": (per_source[sid]["loudness"] or {}).get("points", []),
                    "duration": job.sources[sid].duration or 0.0,
                }
                for sid in video_sources
            },
            job.prefs,
        )
        plan = validate_plan(EditPlan.model_validate(raw), job.prefs, sources=sources_for_validation)
        meta["source"] = "fallback"

    # subtitle burn-in across cut/concat segments isn't built yet -- the
    # renderer rejects an enabled-subtitles plan, so gate it here until the
    # Phase 5 subtitle renderer lands. Transcription still runs as evidence.
    plan.subtitles.enabled = False

    log.info("source=%s attempts=%d retries=%d", meta["source"], meta["attempts"], meta["retries"])
    meta["cost_usd"] = _cost(meta)
    dynamo.update_job(
        jobs_table, job_id, edit_plan=plan.model_dump(), planning=meta, status="RENDERING"
    )
    events.publish(
        "job.planned",
        job_id,
        user_id=job.user_id,
        status="RENDERING",
        source=meta["source"],
        model=meta["model"],
        input_tokens=meta["input_tokens"],
        output_tokens=meta["output_tokens"],
        cost_usd=meta["cost_usd"],
    )
    dynamo.finish_step(jobs_table, job_id, "plan")


def _plan_with_llm(
    planner: BedrockPlanner,
    evidence: dict,
    prompt_prefs: dict,
    prefs: dict,
    known_sources: list[str],
    valid_tracks: set[str],
    sources_for_validation: dict[str, SourceBounds],
    log,
) -> tuple[EditPlan | None, dict]:
    meta = {
        "source": "llm",
        "model": planner.model_id,
        "input_tokens": 0,
        "output_tokens": 0,
        "attempts": 0,
        "retries": 0,
    }
    messages = prompt.build_messages(evidence, prompt_prefs)

    for attempt in (0, 1):
        try:
            raw, usage = planner.generate(messages)
        except (BotoCoreError, ClientError):
            return None, meta  # Bedrock unavailable -> deterministic fallback
        meta["attempts"] += 1
        meta["input_tokens"] += usage["input_tokens"]
        meta["output_tokens"] += usage["output_tokens"]

        raw = _remap_unknown_sources(raw, known_sources, log)
        raw = _drop_unknown_music(raw, valid_tracks, log)
        raw = _drop_micro_clips(raw, log)
        plan, errors = _try_validate(raw, prefs, sources_for_validation)
        if plan is not None:
            return plan, meta
        # structural/schema error strings only -- never plan content, which can
        # carry transcript-derived text (docs/DESIGN.md §10 layer 7)
        log.warning("attempt %d rejected: %s", attempt, errors)
        if attempt == 0:
            meta["retries"] = 1
            messages = prompt.retry_messages(evidence, prompt_prefs, raw or {}, errors)

    return None, meta


def _remap_unknown_sources(raw: dict | None, known_sources: list[str], log) -> dict | None:
    # The model sometimes invents a `source` name (e.g. "source_clip") that the
    # renderer can't resolve, crashing the Cut step. With a single source there
    # is no ambiguity -- any reference can only mean that one, so remap it.
    # Multi-source (v1.2) is not v1.0 scope; there a bad source stays invalid
    # and is caught by validate_plan's cross-file checks instead (ADR-2).
    if raw is None or not isinstance(raw.get("clips"), list) or len(known_sources) != 1:
        return raw
    sole = known_sources[0]
    remapped = 0
    clips = []
    for clip in raw["clips"]:
        if isinstance(clip, dict) and clip.get("source") != sole:
            clip = {**clip, "source": sole}
            remapped += 1
        clips.append(clip)
    if remapped:
        log.info("remapped %d clip source(s) to '%s'", remapped, sole)
        return {**raw, "clips": clips}
    return raw


def _drop_unknown_music(raw: dict | None, valid_tracks: set[str], log) -> dict | None:
    # music_track is a free string the renderer resolves against the bundled
    # manifest (or a job-scoped "user:<src_id>" asset) -- an invented id
    # (e.g. "upbeat-01") raises there and fails the render. Drop an unknown
    # track to no-music rather than crash.
    if raw is None or not isinstance(raw.get("audio"), dict):
        return raw
    track = raw["audio"].get("music_track")
    if track is None or track in valid_tracks:
        return raw
    log.info("dropped unknown music_track '%s'", track)
    return {**raw, "audio": {**raw["audio"], "music_track": None}}


def _drop_micro_clips(raw: dict | None, log) -> dict | None:
    # The model persistently anchors sub-0.5s clips to (often noisy) scene cuts
    # and ignores the min-length rule even when the retry names it. Rather than
    # let one 0.3s clip reject the whole plan into fallback, drop just the too-
    # short clips here, on the LLM output. The validator stays strict -- this
    # repairs the plan before it reaches the boundary, it does not relax it.
    if raw is None or not isinstance(raw.get("clips"), list):
        return raw
    kept = [c for c in raw["clips"] if not _is_micro_clip(c)]
    if len(kept) == len(raw["clips"]):
        return raw
    log.info("dropped %d clip(s) shorter than %ss", len(raw["clips"]) - len(kept), MIN_CLIP_SECONDS)
    return {**raw, "clips": kept}


def _is_micro_clip(clip: dict) -> bool:
    try:
        return (clip["end"] - clip["start"]) < MIN_CLIP_SECONDS - DURATION_EPSILON
    except (KeyError, TypeError):
        return False  # malformed shape -- let the validator reject it


def _try_validate(
    raw: dict | None, prefs: dict, sources: dict[str, SourceBounds]
) -> tuple[EditPlan | None, list[str]]:
    if raw is None:
        return None, ["model returned no tool output"]
    try:
        plan = EditPlan.model_validate(raw)
    except ValidationError as exc:
        return None, [f"{e['loc']}: {e['msg']}" for e in exc.errors()]
    try:
        return validate_plan(plan, prefs, sources=sources), []
    except EditPlanValidationError as exc:
        return None, exc.errors


def _cost(meta: dict) -> float:
    price_in, price_out = NOVA_PRICE_PER_MTOK.get("nova-lite", (0.0, 0.0))
    for key, price in NOVA_PRICE_PER_MTOK.items():
        if key in meta["model"].replace("_", "-").lower():
            price_in, price_out = price
    return round(meta["input_tokens"] / 1e6 * price_in + meta["output_tokens"] / 1e6 * price_out, 6)


def _load(analysis_keys: dict, category: str, src_id: str, work_bucket: str, tmp_dir: Path) -> dict | None:
    entry = analysis_keys.get(category)
    if not entry or src_id not in entry:
        return None
    local = storage.download(work_bucket, entry[src_id], tmp_dir)
    return json.loads(local.read_text())


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    with log_job(__name__, job_id), segment(__name__, job_id):
        run_plan(
            job_id,
            jobs_table=os.environ["JOBS_TABLE"],
            work_bucket=os.environ["WORK_BUCKET"],
            model_id=os.environ.get("NOVA_MODEL_ID", DEFAULT_MODEL_ID),
            region=os.environ.get("BEDROCK_REGION"),
            max_output_tokens=int(os.environ.get("PLAN_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)),
            guardrail_id=os.environ.get("GUARDRAIL_ID") or None,
            guardrail_version=os.environ.get("GUARDRAIL_VERSION") or None,
        )
    return {"job_id": job_id}
