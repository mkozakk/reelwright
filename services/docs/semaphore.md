# Semaphore (`semaphore/`)

Two Lambdas implementing a DynamoDB-backed counting semaphore, not a queue: one item (`SEMAPHORE#render`) holds `held` and a `holders` map of `job_id → acquired_at`.

## Acquire

`acquire_handler` calls `semaphore.acquire_slot`, a conditional `held < cap` increment. On failure it raises `SlotUnavailable`, and the state machine's own `Retry` on that exception (10s interval, up to 90 attempts) **is** the wait: no additional compute runs while a job is queued. `RENDER_CONCURRENCY_CAP` defaults to 2.

## Release

`release_handler` calls `semaphore.release_slot`, which decrements and removes the job from `holders`. It's called from both the success and failure paths in the state machine, so a slot is always freed regardless of how Render ended.

## Related

- [[pipeline]]: `AcquireRenderSlot` and the two `ReleaseSlot*` states this service backs
- [[common]]: `semaphore.py`, the actual acquire/release logic this service wraps
- [[render]]: the Fargate task this concurrency cap protects
