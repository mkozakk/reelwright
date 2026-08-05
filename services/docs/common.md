# Common Library (`common/`)

Not a deployable service itself, but imported by every handler in `services/`. No `boto3` call outside `services/` happens anywhere but here, plus a small number of API-specific calls (`job_api/handler.py`'s S3 presigning, `plan/bedrock_planner.py`'s Bedrock client, `finish/cloudfront_sign.py`'s signing) that are specific enough not to be worth abstracting further.

## Modules

| Module | Provides |
|---|---|
| `models.py` | `JobRecord`, `SourceRef`: plain dataclasses giving the typed view of a DynamoDB job item |
| `dynamo.py` | all job-table reads/writes: `get_job`, `update_job`, `conditional_status_flip`, `claim_ip_slot`/`claim_user_slot` (atomic daily quota counters), `start_step`/`finish_step` (per-step timing), `set_analysis_key`/`set_cut_key`/`update_source` (concurrency-safe nested-leaf writes), `to_decimal`/`to_native` (float to DynamoDB `Decimal` and back) |
| `semaphore.py` | the render-concurrency counting semaphore that `services/semaphore/` wraps |
| `storage.py` | S3 `exists`/`download`/`upload`/`list_keys`, one shared implementation instead of every service calling `boto3.client("s3")` itself |
| `s3keys.py` | every S3 key-naming convention in one place, so no service constructs a key inline |
| `cutcache.py` | `cache_key`/`profile_key`, the content-addressing scheme behind Cut's cache |
| `session_caps.py` | `MAX_FILES = 5`, `MAX_SESSION_VIDEO_SECONDS = 300.0`, enforced by both `job_api` and `session_profile` |
| `events.py` | `publish(detail_type, job_id, **detail)`, the one function every `job.*` EventBridge event goes through |
| `logging.py` | `get_logger`/`log_job`, structured JSON log lines; `log_job` is a context manager logging start/done/failed around a handler body |
| `tracing.py` | `segment()`, an X-Ray subsegment context manager, safe to use outside a live Lambda trace (for example in tests) |

## Why nested-leaf writes

`get_job` reconstructs a `JobRecord` from a raw DynamoDB item. Every write path except the very first (`put_new_job`) goes through targeted `update_item` calls rather than read-modify-write, specifically so concurrent Map iterations, different sources in `ProbeMap`, different categories in `AnalyzeParallel`, different batches in `CutMap`, can write their own leaf of the same job item without clobbering each other's writes.

## Related

- [[job-api]]: the heaviest consumer of `dynamo.py`'s quota functions
- [[cut]]: uses `cutcache.py` directly
- [[pipeline]]: `start_step`/`finish_step` back the per-step timing shown across the whole pipeline
