from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from renderer.edit_plan.models import EditPlan
from renderer.edit_plan.validate import EditPlanValidationError, validate_plan
from services.common import dynamo, storage

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
) -> None:
    dynamo.update_job(jobs_table, job_id, status="PLANNING")

    job = dynamo.get_job(jobs_table, job_id)
    src_id = _single_source(job.analysis_keys)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        loudness = _load(job.analysis_keys, "loudness", src_id, work_bucket, tmp_dir)
        scenes = _load(job.analysis_keys, "scenes", src_id, work_bucket, tmp_dir)
        transcript = _load(job.analysis_keys, "transcript", src_id, work_bucket, tmp_dir)

    evidence = evidence_mod.build_evidence(loudness, scenes, transcript)
    prompt_prefs = evidence_mod.prefs_for_prompt(job.prefs)

    planner = planner or BedrockPlanner(model_id, region, max_output_tokens)
    plan, meta = _plan_with_llm(planner, evidence, prompt_prefs, job.prefs)

    if plan is None:
        raw = fallback_planner.build_plan(src_id, (loudness or {}).get("points", []), job.prefs)
        plan = validate_plan(EditPlan.model_validate(raw), job.prefs)
        meta["source"] = "fallback"

    meta["cost_usd"] = _cost(meta)
    dynamo.update_job(
        jobs_table, job_id, edit_plan=plan.model_dump(), planning=meta, status="RENDERING"
    )


def _plan_with_llm(
    planner: BedrockPlanner, evidence: dict, prompt_prefs: dict, prefs: dict
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

        plan, errors = _try_validate(raw, prefs)
        if plan is not None:
            return plan, meta
        if attempt == 0:
            meta["retries"] = 1
            messages = prompt.retry_messages(evidence, prompt_prefs, raw or {}, errors)

    return None, meta


def _try_validate(raw: dict | None, prefs: dict) -> tuple[EditPlan | None, list[str]]:
    if raw is None:
        return None, ["model returned no tool output"]
    try:
        plan = EditPlan.model_validate(raw)
    except ValidationError as exc:
        return None, [f"{e['loc']}: {e['msg']}" for e in exc.errors()]
    try:
        return validate_plan(plan, prefs), []
    except EditPlanValidationError as exc:
        return None, exc.errors


def _cost(meta: dict) -> float:
    price_in, price_out = NOVA_PRICE_PER_MTOK.get("nova-lite", (0.0, 0.0))
    for key, price in NOVA_PRICE_PER_MTOK.items():
        if key in meta["model"].replace("_", "-").lower():
            price_in, price_out = price
    return round(meta["input_tokens"] / 1e6 * price_in + meta["output_tokens"] / 1e6 * price_out, 6)


def _single_source(analysis_keys: dict) -> str:
    # single source in v1.0; multi-source (v1.2) merges evidence across sources
    return next(iter(analysis_keys["loudness"]))


def _load(analysis_keys: dict, category: str, src_id: str, work_bucket: str, tmp_dir: Path) -> dict | None:
    entry = analysis_keys.get(category)
    if not entry or src_id not in entry:
        return None
    local = storage.download(work_bucket, entry[src_id], tmp_dir)
    return json.loads(local.read_text())


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    run_plan(
        job_id,
        jobs_table=os.environ["JOBS_TABLE"],
        work_bucket=os.environ["WORK_BUCKET"],
        model_id=os.environ.get("NOVA_MODEL_ID", DEFAULT_MODEL_ID),
        region=os.environ.get("BEDROCK_REGION"),
        max_output_tokens=int(os.environ.get("PLAN_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)),
    )
    return {"job_id": job_id}
