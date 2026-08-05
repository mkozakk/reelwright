# Render (`render/`)

Not a Lambda: an ECS/Fargate task (`__main__.py` calling `main.py`), the one compute step in the pipeline that isn't a Lambda function.

## What it does

Reads `JOB_ID` from the environment, injected by the state machine's `ContainerOverrides`. Downloads every cut segment via `job.cut_keys`, read as a map rather than listed by prefix, which is what lets a rerender mix cache-hit and freshly-cut segments from different originating jobs. Rewrites them into a concat-ready plan via `renderer.segments.build_concat_plan`, resolves a `user:<src_id>` music track from this job's own work-bucket assets if one is set, and calls `renderer.compile.compile_plan` plus `run_ffmpeg` directly: the same library calls Cut makes, just on pre-cut, pre-normalized inputs.

## Subtitles aren't wired up yet

Raises `NotImplementedError` if `subtitles.enabled`. This isn't the primary gate, since `plan/` already force-disables subtitles before a plan ever reaches here; it's a defensive check for the day burn-in across the Cut/Render segment boundary actually gets implemented.

## Failure and retry

On any exception, `main()` catches it, marks the job `FAILED`, and returns a nonzero exit code. The state machine's `RenderSpot`/`RenderOnDemand` tasks use that exit code, via `ecs:runTask.sync`, to decide whether to fall through to the on-demand fallback. This task is requested first on Fargate Spot; a failure there falls through to on-demand rather than retrying Spot again (see [[pipeline]]).

## Related

- [[cut]]: produces the segments this task downloads and reassembles
- [[compile]] (renderer): the function this task calls directly, no CLI subprocess involved
- [[semaphore]]: the concurrency gate wrapped around this task in the state machine
- [[finish]]: the next step once this task's output lands in the output bucket
