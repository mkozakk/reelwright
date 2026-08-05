# 0002: One planning model, no quality/Pro toggle

## Status

Accepted.

## Context

Bedrock offers Nova Lite and Nova Pro as options for the planning step. The obvious product shape is a `quality` job preference: Lite for fast/cheap jobs, Pro for a "better" edit when a user asks for it.

Before building that toggle, a spike (`tools/spikes/phase4_planning/`) measured both models against the actual Edit Plan validator: forced-JSON success rate, retry rate, and cost per plan on the same evidence bundles.

## Decision

Ship Nova Lite only. There is no `quality` job preference and no Pro code path.

The spike found Nova Pro produced a worse result on the metric that actually matters here (0% fallback rate for Lite vs. 60% fallback rate for Pro under the strict validator), while costing roughly 20x more per plan. "Better model, worse structured output" is a real failure mode with forced-JSON tool-use, not a hypothetical, and it showed up directly in measurement.

A future higher-quality tier is left open as a possibility, but scoped as a different model family entirely (Claude-class), to be benchmarked as its own decision rather than added as a Nova Lite/Pro toggle.

## Consequences

- One code path for planning, one model id in config, no branching on a `quality` preference anywhere in the plan Lambda or the frontend.
- Per-job Bedrock cost stays at the Lite price point (~$0.0003/plan measured in the spike) for every job, not just the default tier.
- Reopening a "premium" planning tier later means adding a new model integration and re-running this kind of validator-driven comparison, not just flipping on Nova Pro.
