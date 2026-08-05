# Trigger (`trigger/`)

A dev-only convenience path: it fires on an EventBridge "S3 Object Created" event for `raw/<job_id>/<src_id>` and starts the pipeline without the client having to call `POST /jobs/{id}/start`.

## Why it backs off for multi-file sessions

`ObjectCreated`-per-file can't safely start a multi-file session: the first upload would fire the pipeline before the rest of the declared files exist. `handler.handler` checks `len(job.sources) != 1` before doing anything else and, if the job has more than one declared source, logs `"multi-file session, ignoring EventBridge convenience trigger"` and returns without touching the job's status. A real multi-file session is only ever started by `job_api`'s `POST /jobs/{id}/start`, which verifies every file exists first.

## Single-file path

For a single-source job, `trigger/` does the same conditional `UPLOADING → ANALYZING` status flip and `StartExecution` call that `job_api/_start_job` does, keyed on the same idempotent execution-name hash pattern, so a genuine double-start (one from the EventBridge trigger, one from a client calling `/start` anyway) is still a no-op.

## Related

- [[job-api]]: owns the equivalent, safe-for-multi-file start path
- [[pipeline]]: the state machine this service starts
