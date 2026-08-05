# Storage (`s3.tf`, `dynamodb.tf`, `ecr.tf`)

## Buckets

Three buckets (`raw`, `work`, `output`), each with a public-access block, AES256 encryption, and a lifecycle expiration tuned to how long that stage's data actually needs to live:

| Bucket | Expiration | Notes |
|---|---|---|
| `raw` | 2 days | `eventbridge = true` notifications feed the dev-only trigger path; CORS scoped to `PUT`/`var.frontend_origin`, since the browser uploads straight to a presigned URL |
| `work` | 7 days | intermediate analysis artifacts, cut segments, the cut cache |
| `output` | 30 days | finished montages and thumbnails |

All three carry an abort-incomplete-multipart-upload rule at 2 days, and all three are `force_destroy = true`, since this is disposable dev infra and a `terraform destroy` must never block on leftover job objects.

## The jobs table

One DynamoDB table (`jobs`), `PAY_PER_REQUEST`, hash key `pk`, one GSI (`GSI1`: `user_id` plus `created_at`, backing `GET /jobs`'s per-user list), TTL on the `ttl` attribute, point-in-time recovery on. This single table is the entire data model; every service reads and writes it through `services/common/dynamo.py`.

## ECR repos

Three repos, split by what actually needs to be in the image:

- **`lambda`**: the shared image behind `probe`, `cut`, `finish`, `plan`, `job_api`, and `analytics_sink`.
- **`render-task`**: the Fargate render image.
- **`analyze-transcribe`**: its own image, since faster-whisper plus baked model weights are multi-hundred-MB and irrelevant to every other function's cold start.

Each has scan-on-push and a lifecycle policy: untagged images expire after 1 day, and only the last 10 tagged images are kept.

## Related

- [[compute]]: the Lambdas and Fargate task these buckets and repos feed
- [[bootstrap]]: the separate state-backend bucket this file's resources don't touch
