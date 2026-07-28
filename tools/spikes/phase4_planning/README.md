# Phase 4 spike — forced-JSON + validation-retry rate on Nova

Phase 4's first checklist item is a gate: **verify the forced-JSON mechanism on
Nova and measure the validation-retry rate before building fixtures around the
Nova Lite default.** If retries turn out to be the *common* path on Lite rather
than the exception, planning cost and latency roughly double and the Lite
default gets revisited (see `docs/phases/phase-4.md`).

This script answers two questions:

1. **Does forced JSON work on Nova, and by which mechanism?** — tool-use
   forcing (`toolChoice: {tool}` with the Edit Plan schema as the tool's
   `inputSchema`) vs. prompt + parse. Bedrock has no universal
   schema-constrained decoding, so this is measured, not assumed.
2. **What fraction of plans need the validation retry?** — the real
   `renderer.edit_plan.validate` validator judges every plan, and failures are
   categorized (overlap / max_duration / min_clip / transition / shape) so the
   *reason* for retries is visible, not just the rate.

## What it feeds the model

A genuinely real evidence bundle assembled from committed fixtures — nothing
hand-invented:

| evidence     | source                                   | via                          |
|--------------|------------------------------------------|------------------------------|
| loudness ~1 Hz | `tests/fixtures/ebur128_clip_a.txt`    | `renderer.loudness`          |
| scene cuts   | `tests/fixtures/scdet_clip_a.txt`        | `renderer.scenes`            |
| phrases      | `assets/sample/transcript_a.json`        | word→phrase grouping (local) |

The Edit Plan JSON Schema handed to Bedrock is generated live from the pydantic
model (`EditPlan.model_json_schema()`) — there is no committed `schema.json`
yet; that generator lands later this phase.

## Run it

Offline self-test (no AWS — proves the harness, evidence parsing, and retry
loop; one iteration is rigged to fail-then-recover):

```
python tools/spikes/phase4_planning/spike.py --dry-run
```

Against real Bedrock (needs AWS creds + Nova model access enabled in the
region). This costs a few cents total and hits your account:

```
python tools/spikes/phase4_planning/spike.py --iterations 30
python tools/spikes/phase4_planning/spike.py --iterations 30 --mechanism prompt
python tools/spikes/phase4_planning/spike.py --iterations 30 --model-id amazon.nova-pro-v1:0
python tools/spikes/phase4_planning/spike.py --iterations 30 --model-id <claude-haiku-id>
```

Flags: `--mechanism {tool-use,prompt}`, `--model-id`, `--region`,
`--temperature` (default 0.4 so repeated iterations sample varied output),
`--max-tokens`, `--iterations`.

## Decision criterion

The script prints a `DECISION SIGNAL` line. Threshold: if **retry-needed > 50%**
it flags Nova Lite as the *common retry path* → revisit the Lite default before
building fixtures. Otherwise the Lite default holds and Phase 4 proceeds.

## Results (2026-07, single fixture: clip_a)

Final config = Nova-tuned schema (`_deref` + `_simplify_for_nova`) + prompt
hardened on the minimum-clip rule. 30 iterations each.

| model | mechanism | first-try valid | retry needed | fallback | top failure cat | $/plan |
|-------|-----------|-----------------|--------------|----------|-----------------|--------|
| **nova-lite** | **tool-use** | **87%** | **13%** | **0%** | transition (3) | **$0.00031** |
| nova-pro  | tool-use | 37% | 63% | **60%** | overlap (12), min_clip (7) | $0.00630 |
| nova-lite | prompt   | 0%  | 100% | 100% | schema_shape (30) | $0.00040 |

Progression on Nova Lite tool-use as fixes landed:

| schema / prompt | first-try | fallback | top failure |
|-----------------|-----------|----------|-------------|
| raw ref'd schema, base prompt | 63% | 23% | min_clip (9) |
| deref'd schema, base prompt   | 47% | 30% | min_clip (14) |
| simplified schema, hardened prompt | **87%** | **0%** | transition (3) |

### Findings

1. **Mechanism: tool-use forcing.** Prompt+parse is 0% first-try on Nova
   (`schema_shape` every time) — unusable. Not a tuning question; tool-use is
   the only viable channel.
2. **The schema must be Nova-tuned.** Raw `model_json_schema()` crashed Nova
   Pro (`Model produced invalid sequence as part of ToolUse`); `$ref` inlining
   alone did not fix it. Collapsing `anyOf:[X,null]` unions and stripping
   `title`/`default` (`_simplify_for_nova`) was required for Pro to run and is
   the schema Lite scores 87% on.
3. **Prompt hardening solved min_clip.** The 0.5s minimum is a cross-field
   rule JSON Schema can't express; the model only sees it in prose. A stronger
   rule + a concrete good/bad example + "fewer clips, never shorter" drove
   min_clip failures from 9–14 to **0** and fallback from 23–30% to **0%** on
   Lite. Validator stayed strict (reject→retry→fallback), unchanged.
4. **Nova Pro is worse AND 20× dearer than Lite here.** Pro fell back on 60%
   of plans (dominated by same-source overlaps — it "reuses" moments) at
   $0.0063/plan vs Lite's 0% fallback at $0.00031. Under the strict validator,
   Pro is disqualified as the `quality: pro` upgrade on this fixture.

### Decision

- **Nova Lite + tool-use forcing is the default planner.** Gate passed:
  retry is the exception (13%), fallback 0%.
- **Flag for `docs/`:** the `quality: pro → Nova Pro` assumption (DESIGN.md §4)
  is contradicted here — Pro produces *less* schema-valid output. Either
  investigate a Pro-specific prompt (its failure is overlaps, not min_clip) or
  reconsider what the `pro` tier maps to. Not blocking Lite.

### Caveat — single fixture

All numbers are one evidence bundle (`clip_a`: 8 loudness pts, 89 noisy scene
cuts, 2 phrases). The mechanism findings (1–3) are robust; the Lite-vs-Pro
quality gap (4) should be re-confirmed on richer/more varied evidence before
it's written into the docs as final.

First run (2026-07, raw schema): prompt mode is unusable on Nova (0% first-try);
tool-use forcing is the mechanism. Nova Pro crashed on the raw ref'd schema —
re-run after the `_deref` fix.

## Known caveats to watch on the real run

- **tool-use schema support:** `model_json_schema()` emits `$defs`/`$ref`
  plus `anyOf:[X,null]` unions (from Optional fields) and `title`/`default`
  noise. Nova Lite tolerated it; **Nova Pro crashed** with
  `Model produced invalid sequence as part of ToolUse` — twice, and `_deref`
  alone did not fix it. `edit_plan_schema()` now also runs
  `_simplify_for_nova`: inline refs, collapse nullable unions to their real
  branch, strip `title`/`default`. Re-run Pro after this.
- **scdet noise:** the low-threshold scdet fixture yields ~89 scene cuts on a
  low-motion clip. That inflates the evidence bundle; real Phase 4 evidence
  compression should cap/rank scene cuts. Noted, not fixed here.
- **`toolChoice: {tool}`** may need to be `{any}` on some Nova versions — try
  `--mechanism prompt` as the fallback comparison if tool forcing errors.
