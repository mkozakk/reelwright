# Pipeline Flow

One Step Functions state machine, defined in `infra/envs/dev/statemachine.asl.json.tpl`, orchestrates every Lambda and the Fargate render task. `job_api/` and `trigger/` sit outside it; they're what starts an execution, not a step in it.

## The state-by-state walk

```
RouteMode (Choice: mode == "rerender"?)
  │
  ├─ new job ──▶ ProbeMap (Map, per source)
  │                └─ probe/
  │              ▶ SessionValidate
  │                └─ session_profile/
  │              ▶ AnalyzeParallel (Parallel, 3 branches)
  │                ├─ TranscribeChoice → TranscribeMap (Map, per video source, skipped if !subtitles_enabled)
  │                │    └─ analyze_transcribe/
  │                ├─ LoudnessMap (Map, per video source)
  │                │    └─ analyze_loudness/
  │                └─ ScenesMap (Map, per video source)
  │                     └─ analyze_scenes/
  │              ▶ Plan
  │                └─ plan/
  │
  └─ rerender ─────────────────────────────────────▶ PrepareCut
                                                        └─ cut/prepare.py
                                                      ▶ CutMap (Map, per batch of 5 clips)
                                                        └─ cut/
                                                      ▶ AcquireRenderSlot (Retry loop = the wait)
                                                        └─ semaphore/ (acquire)
                                                      ▶ RenderSpot (Fargate Spot)
                                                        └─ render/
                                                        ├─ on failure → RenderOnDemand (Fargate on-demand)
                                                      ▶ ReleaseSlotSuccess / ReleaseSlotFailure
                                                        └─ semaphore/ (release)
                                                      ▶ Finish
                                                        └─ finish/
                                                      ▶ End

any Catch: States.ALL ─▶ JobFailed → PublishJobFailedEvent → SendToDlq → Fail
```

A rerender skips analysis and planning entirely and jumps straight to `PrepareCut`, since a rerender only ever changes the Edit Plan itself (edited by the user, or resubmitted as-is), not the underlying evidence.

## Failure handling

Every state's `Catch` routes to the same three-step tail: `JobFailed` writes `status = FAILED` to DynamoDB, `PublishJobFailedEvent` fires a `job.failed` event onto the EventBridge bus, and `SendToDlq` pushes the full execution input onto an SQS dead-letter queue. A `dlq_depth` CloudWatch alarm fires on anything landing there, since an unwatched DLQ is a black hole.

## Render retries

`RenderSpot` requests Fargate Spot capacity first; if that task fails, its `Catch` routes to `RenderOnDemand` on regular Fargate rather than retrying Spot again. `AcquireRenderSlot`'s own `Retry` on `SlotUnavailable` (10s interval, up to 90 attempts) is what implements the concurrency wait: no additional compute runs while a job is queued for a render slot.

## Related

- [[job-api]] and [[trigger]]: the two entry points that start an execution
- [[semaphore]]: the acquire/release pair around the two Render states
- [[render]]: the Fargate task both `RenderSpot` and `RenderOnDemand` run
