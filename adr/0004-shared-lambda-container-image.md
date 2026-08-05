# 0004: One shared Lambda container image, not one per function

## Status

Accepted.

## Context

`probe`, `cut`, `finish`, and `plan` all need ffmpeg or the `renderer/` package's dependencies (pydantic at minimum), so they're packaged as Lambda container images rather than zip archives. `trigger` needs neither; it only imports `services/` and the runtime's built-in boto3, so it stays a plain zip.

The question is whether each ffmpeg-dependent function gets its own image, or whether they share one. ffmpeg images are large, and CI builds and pushes on every commit. Near-identical images per function multiply both push time and ECR storage for no real benefit, since the four functions are running the same ffmpeg binary and the same `renderer/` package underneath different handler entrypoints.

## Decision

`probe`, `cut`, `finish`, and `plan` share a single container image (built from `docker/lambda.Dockerfile`), with each Lambda function pointing at the same image digest but a different handler `CMD`. The Fargate renderer keeps its own separate image with a `render-task` entrypoint, since it runs on a different compute platform with different runtime concerns (no 15-minute ceiling, no cold start budget).

`plan` joins this shared image even though it needs no ffmpeg at all, because it imports `renderer/` for schema validation, which pulls in pydantic. That dependency isn't in the base Lambda runtime, so it would need a built dependency bundle either way; joining the existing image is cheaper than maintaining a second one just for pydantic.

## Consequences

- One CI build and push per commit for the shared image instead of four, and one ECR repo's lifecycle policy to manage instead of four.
- "Which ffmpeg build ran this job" has exactly one answer across probe, cut, finish, and plan, which matters for reproducing a render later.
- The four functions are coupled at the image level: a change to any one handler's dependencies rebuilds and redeploys the image for all four, even ones that didn't change. That coupling was accepted as a smaller cost than the alternative CI/ECR overhead, given how small this deployment is.
