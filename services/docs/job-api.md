# Job API (`job_api/`)

The only service with an HTTP-facing handler (API Gateway HTTP API, `{proxy+}`). Routes are dispatched by method and path shape in `handler.handler`, not a router library.

## Endpoints

| Route | Handler function | Does |
|---|---|---|
| `POST /jobs` | `_create_job` | validates the request, claims per-IP and per-user daily quota slots, creates the DynamoDB job item, returns one presigned upload per declared file |
| `POST /jobs/{id}` | `_complete_upload` | completes a multipart upload |
| `POST /jobs/{id}/start` | `_start_job` | HeadObjects every declared source, flips `UPLOADING → ANALYZING`, starts the state machine |
| `POST /jobs/{id}/rerender` | `_rerender` | validates a client-edited Edit Plan, claims a fresh quota slot, starts the state machine in `rerender` mode |
| `GET /jobs/{id}` | `_get_job` | job status; once `DONE`, signs CloudFront playback/thumbnail URLs |
| `GET /jobs` | `_list_jobs` | the authenticated user's jobs |

## Ownership is 404, not 403

`_owned_job` gives a non-owner the same "not found" a nonexistent job would return, so the API never confirms a job id exists to someone who doesn't own it.

## Idempotency

`start_execution_name`/`rerender_execution_name` (`logic.py`) hash the job id plus source ETags/prefs, or plus the edited plan JSON, into the Step Functions execution name. A duplicate `/start` or an identical `/rerender` submit is a no-op: `ExecutionAlreadyExists` is caught and swallowed instead of starting a second pipeline run.

## Quota rails

`claim_ip_slot` and `claim_user_slot` (`services/common/dynamo.py`) are two independent atomic per-day DynamoDB counters. The IP cap (`IP_DAILY_CAP = 20`) is defense-in-depth; the user cap (`USER_DAILY_CAP = 3`) is the real quota now that jobs are authenticated. A re-render claims a slot too, since it's a render.

## Multipart uploads

Files over `MULTIPART_THRESHOLD` (100 MB) get a presigned URL per 16 MB part instead of one presigned PUT (`logic.wants_multipart`/`part_count`).

## Structure

`logic.py` holds every pure validation and business-logic function (`validate_create_request`, `validate_complete_request`, `build_job_item`, and so on); `handler.py` is the AWS-facing shell around it, split the same way `services/` and `renderer/` are split at the package level.

## Related

- [[pipeline]]: the state machine `_start_job` and `_rerender` both start
- [[common]]: `dynamo.py`'s quota counters and `s3keys.py`'s key layout
- [[finish]]: produces the `output_key`/`thumbnail_key` this service signs URLs for
