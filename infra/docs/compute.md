# Compute (`lambda.tf`, `ecs.tf`)

Twelve Lambda functions, packaged two different ways depending on what they actually need.

## Plain zip

`trigger`, `semaphore_acquire`/`release`, and `session_profile` only need `boto3` plus `services/common`, no `ffmpeg`, so a container image would be pure overhead. Built by `scripts/build_lambda_zips.sh` before `terraform apply`; the `.zip`s live in `envs/dev/build/`, gitignored.

## Container image

Everyone else: `package_type = "Image"`, pinned by **digest** (`data.aws_ecr_image`) rather than the mutable `:latest` tag, so a re-push under the same tag is picked up automatically on the next `apply`.

`analyze_transcribe` gets its own image, `architectures = ["x86_64"]` (pending an arm64 benchmark), and `memory_size = 3008`, which isn't a rounder 4096 because this account's per-function `CreateFunction` ceiling is 3008 MB, an account-level restriction rather than a sizing choice. faster-whisper `small` int8 still fits comfortably under 3 GB.

Every function sets `tracing_config { mode = "Active" }` for X-Ray. The VPC-isolated functions (`probe`, `analyze_loudness`, `analyze_scenes`, `analyze_transcribe`, `cut`, `cut_prepare`) get `vpc_config` into the private subnets plus the `isolated_lambda` security group; `plan`, `finish`, `job_api`, `semaphore`, `session_profile`, and `trigger` have no `vpc_config`, since they only touch DynamoDB, S3 via the SDK, Bedrock, or CloudFront signing, none of which needs isolation from raw media.

## Fargate

One cluster, with both `FARGATE` and `FARGATE_SPOT` capacity providers registered (required, or `runTask.sync` rejects a `CapacityProviderStrategy` naming either). One task definition (`render`, 4 vCPU / 8 GB); the state machine's two Render states pick Spot versus on-demand purely via `CapacityProviderStrategy` at `runTask` time, not two separate task defs.

The container has a sidecar, `xray-daemon`, that the render container's `tracing.segment()` calls reach over UDP on localhost, since both containers share the same task network namespace.

## Related

- [[networking]]: the subnets and security groups these functions run inside
- [[orchestration]]: what actually invokes each function and the Fargate task
- [[render]] (services): the code running inside the Fargate task defined here
