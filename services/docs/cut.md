# Cut (`cut/`)

Two Lambdas: one that splits the plan into batches, and one that does the actual re-encoding, one Map iteration per batch.

## Preparing batches (`prepare.py`, the `PrepareCut` state)

Loads the validated plan and splits its clip indices into batches of `CLIP_BATCH_SIZE = 5` (`build_batches`), one Map iteration per batch, so a plan with many clips fans out across several concurrent Lambda invocations instead of one long serial loop.

## Cutting (`handler.py`, `run_cut`, the `CutMap` state)

For each clip index in its batch:

1. Computes a content-addressed cache key (`cutcache.cache_key`: source S3 key plus start/end/speed plus aspect/resolution profile, sha256'd).
2. Skips re-encoding entirely if that key already exists in the work bucket (`work/cache/<hash>.mp4`). This cache is shared across a job's own re-renders and, incidentally, across jobs that happen to cut an identical segment.
3. On a cache miss, downloads the raw source once per batch (`source_cache`, shared across clips from the same source within a batch), builds a one-clip plan via `renderer.segments.build_segment_plan`, and calls `renderer.compile.compile_plan` plus `run_ffmpeg` directly.

## Always re-encodes

Cut never stream-copies. This gives frame-accurate cuts, uniform inputs for Render's `xfade`, and means Render never has to touch raw, potentially attacker-controlled upload bytes.

## Related

- [[plan]]: produces the plan this service splits and cuts
- [[segments]] (renderer): the per-clip plan builder this service calls
- [[render]]: consumes the cut segments this service writes to the work bucket
- [[common]]: `cutcache.py`'s content-addressing scheme
