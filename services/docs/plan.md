# Planning (`plan/`)

The most involved service in the pipeline, and the only place an LLM call happens. Split into five files, orchestrated by `handler.py`.

## The five files

- **`handler.py`** (`run_plan`) orchestrates the whole step: loads every video source's loudness/scenes/transcript artifacts, builds the evidence bundle and music catalog (bundled tracks plus `user:<src_id>` for uploaded audio assets), calls the planner, falls back on failure, force-disables subtitles (burn-in across the Cut/Render segment boundary isn't implemented yet, though transcription still runs so the evidence exists), and writes the final plan plus `planning` metadata to DynamoDB.
- **`evidence.py`** (`build_evidence`) turns raw per-source artifacts into the compact bundle the prompt embeds. Every loudness point, scene cut, and phrase carries its own `source` id so the model can select and interleave moments across sources. Scene cuts are capped per source (`MAX_SCENES = 40`, `MIN_SECONDS_PER_SCENE = 2.0`) so one long source can't flood the evidence and starve the others.
- **`prompt.py`** holds the system prompt: editorial rules (minimum clip length, duration budget, no-overlap, `reason` must cite evidence, `source`/`music_track` must be real ids) wrapped in a "DATA, never instructions" framing around the evidence block, so nothing embedded in a transcript can override the rules, plus `retry_messages` for one corrective round.
- **`schema_tool.py`** derives Bedrock's tool-use `inputSchema` from `EditPlan.model_json_schema()`. Nova's tool-use rejects `$ref`/`$defs` (`_deref` inlines every reference) and chokes on `anyOf:[X, null]` Optional-field unions (`_simplify_for_nova` collapses them to the real branch, since optionality is carried by `required` instead).
- **`bedrock_planner.py`** (`BedrockPlanner`) wraps `bedrock-runtime.converse()` with `toolChoice` forced to the one `emit_edit_plan` tool, so the model can't return free text, plus optional Guardrails config.
- **`fallback_planner.py`** (`build_plan`) is the deterministic, no-LLM path: ranks loudness peaks globally across every source, takes a ±3s window around each of the top N, and skips a candidate that would overlap one already picked on the same source.

## Repair passes

Three passes run on the raw LLM tool output **before** validation, each logged, none loosening `validate_plan` itself:

- `_remap_unknown_sources`: single-source jobs only, since an invented source name is unambiguous when there's only one real source it could mean.
- `_drop_unknown_music`: an invented `music_track` id degrades to no music rather than crashing the render.
- `_drop_micro_clips`: drops individual sub-0.5s clips rather than rejecting the whole plan into fallback over one bad clip.

## When fallback kicks in

`fallback_planner.build_plan` is used when Bedrock is unreachable (`BotoCoreError`/`ClientError`) or both LLM attempts, the original plus one corrective retry, fail validation.

## Related

- [[analyze]]: produces the loudness/scenes/transcript artifacts `evidence.py` bundles
- [[edit-plan]] (renderer): the schema this service's output is validated against
- [[cut]]: the next step, consuming the plan this service writes to DynamoDB
