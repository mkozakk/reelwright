# Services (`services/`)

Services are thin Lambda and Fargate shells around `renderer/`. Every AWS SDK call (`boto3`) in the whole pipeline lives here; `renderer/` never imports it. Each service directory maps to one Lambda function, or, for `render/`, one Fargate task, wired together by a single Step Functions state machine.

## Directory Structure

```text
services/
├── docs/                    # Full documentation of all service features
├── common/                  # shared library: dynamo, storage, s3keys, semaphore, logging, tracing, events
├── job_api/                 # Lambda: create job, presign upload, get status, rerender
├── trigger/                 # Lambda: single-file dev convenience S3 trigger
├── probe/                   # Lambda: ffprobe + audio extraction (Map, per source)
├── session_profile/         # Lambda: post-probe session-wide validation
├── analyze_loudness/        # Lambda: ebur128 wrapper
├── analyze_scenes/          # Lambda: scdet wrapper
├── analyze_transcribe/      # Lambda: faster-whisper wrapper
├── plan/                    # Lambda: Bedrock call, schema validation, no-LLM fallback
├── cut/                     # Lambda (two): batch prep + segment extraction
├── render/                  # Fargate task: concat, color, subtitles, music
├── semaphore/                # Lambda (two): render concurrency cap
├── finish/                    # Lambda: verify output, thumbnail, signed URL
└── analytics_sink/             # Lambda: job lifecycle events to S3
```

## Key Responsibilities

- **Entry points**: `job_api/` is the only HTTP-facing service; `trigger/` is a dev-only S3-event convenience path. Both start Step Functions executions idempotently.
- **Analysis fan-out**: `probe/`, `session_profile/`, and the three `analyze_*/` services turn raw uploads into the evidence bundle the planner reasons over.
- **Planning**: `plan/` is the one place an LLM call happens, wrapped in schema validation and a deterministic fallback for when it fails.
- **Rendering**: `cut/` re-encodes clips (with a content-addressed cache), `render/` assembles the final video on Fargate, and `semaphore/` caps how many renders run at once.
- **Delivery and observability**: `finish/` verifies the output and signs playback URLs; `analytics_sink/` captures job lifecycle events for Athena.

## Features & Internal Documentation

* **[Pipeline Flow](docs/pipeline.md)** - the full Step Functions state-by-state walk, from upload to signed playback URL, and how failures route to the DLQ.
* **[Job API](docs/job-api.md)** - the REST entrypoint: create, upload, start, rerender, status, list (`job_api/`).
* **[Trigger](docs/trigger.md)** - the single-file dev convenience path, and why multi-file sessions bypass it (`trigger/`).
* **[Probe](docs/probe.md)** - ffprobe validation and audio extraction, one Map iteration per source (`probe/`).
* **[Session Profile](docs/session-profile.md)** - post-probe session-wide cap re-check and the video-only fan-out (`session_profile/`).
* **[Analysis Branches](docs/analyze.md)** - the three structurally identical loudness/scenes/transcribe Lambdas (`analyze_loudness/`, `analyze_scenes/`, `analyze_transcribe/`).
* **[Planning](docs/plan.md)** - the Bedrock tool-use call, the repair passes, and the deterministic fallback (`plan/`).
* **[Cut](docs/cut.md)** - batch preparation and content-addressed segment extraction (`cut/`).
* **[Render](docs/render.md)** - the Fargate entrypoint that assembles the final video (`render/`).
* **[Semaphore](docs/semaphore.md)** - the DynamoDB-backed render concurrency cap (`semaphore/`).
* **[Finish](docs/finish.md)** - output verification, thumbnail, and signed URL (`finish/`).
* **[Analytics Sink](docs/analytics-sink.md)** - job lifecycle events landing in S3 for Athena (`analytics_sink/`).
* **[Common Library](docs/common.md)** - the shared DynamoDB, S3, semaphore, and logging code every service above imports (`common/`).

## Development

```bash
uv sync                                  # or: pip install -e ".[dev,services]"
pytest -m "not media and not whisper"    # unit + moto-mocked AWS
pytest -m "media and not whisper"        # + real ffmpeg against sample clips
```

`tests/conftest.py`'s `aws_stack` fixture spins up an in-memory DynamoDB table and the three S3 buckets via `moto`, so handler logic gets exercised against real DynamoDB/S3 semantics without touching a real account.

## Tech stack

| Concern | Technology |
|---|---|
| Compute | AWS Lambda (zip and container image), AWS Fargate |
| Orchestration | AWS Step Functions |
| Data | DynamoDB (single `jobs` table), S3 (raw/work/output/analytics buckets) |
| Planning model | Amazon Bedrock, Nova Lite, forced JSON via tool-use |
| Tracing | AWS X-Ray (`aws-xray-sdk`) |
| AWS SDK | boto3 |

## Related Components

- **[Renderer](../renderer/README.md)**: the AWS-free package every media-touching service here imports; services never call ffmpeg directly.
- **[Infra](../infra/README.md)**: provisions every Lambda, the Fargate task, the state machine, and the DynamoDB/S3 resources these services read and write.
