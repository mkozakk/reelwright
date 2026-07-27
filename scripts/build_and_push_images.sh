#!/usr/bin/env bash
# deploy order: terraform apply -target=aws_ecr_repository.lambda -target=aws_ecr_repository.render_task
# -target=aws_ecr_repository.analyze_transcribe, then this script, then a plain terraform apply
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_REGION:-$(aws configure get region)}"
PROJECT="${PROJECT_NAME:-reelwright}"
TAG="${1:-latest}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

ENGINE="docker"
command -v docker >/dev/null 2>&1 || ENGINE="podman"

echo "using ${ENGINE}, region ${REGION}, registry ${REGISTRY}, tag ${TAG}"

aws ecr get-login-password --region "$REGION" | "$ENGINE" login --username AWS --password-stdin "$REGISTRY"

"$ENGINE" build -f "$ROOT/docker/lambda.Dockerfile" -t "${REGISTRY}/${PROJECT}/lambda:${TAG}" "$ROOT"
"$ENGINE" push "${REGISTRY}/${PROJECT}/lambda:${TAG}"

"$ENGINE" build -f "$ROOT/docker/render.Dockerfile" -t "${REGISTRY}/${PROJECT}/render-task:${TAG}" "$ROOT"
"$ENGINE" push "${REGISTRY}/${PROJECT}/render-task:${TAG}"

# separate image, faster-whisper + baked model weights
"$ENGINE" build -f "$ROOT/docker/transcribe.Dockerfile" -t "${REGISTRY}/${PROJECT}/analyze-transcribe:${TAG}" "$ROOT"
"$ENGINE" push "${REGISTRY}/${PROJECT}/analyze-transcribe:${TAG}"

echo "pushed ${REGISTRY}/${PROJECT}/lambda:${TAG}, ${REGISTRY}/${PROJECT}/render-task:${TAG}, and ${REGISTRY}/${PROJECT}/analyze-transcribe:${TAG}"
