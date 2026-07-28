# separate image from docker/lambda.Dockerfile -- faster-whisper + baked
# model weights are multi-hundred-MB and irrelevant to every other Lambda's
# cold start, so probe/cut/finish/plan's shared image stays lean.

# x86_64 default until a real arm64-vs-x86_64 wall-clock benchmark exists
# (see the TODO(benchmark) note in README.md) -- flip with
# `--build-arg LAMBDA_ARCH=arm64` once measured, and update
# infra/envs/dev/lambda.tf's analyze_transcribe `architectures` to match.
ARG LAMBDA_ARCH=x86_64

# same base as docker/lambda.Dockerfile, pinned by digest not the mutable
# "3.12" tag. Re-resolve with:
#   skopeo inspect --format '{{.Digest}}' docker://public.ecr.aws/lambda/python:3.12
FROM --platform=linux/${LAMBDA_ARCH} public.ecr.aws/lambda/python:3.12@sha256:0628ddc11919f85f4261517d356392dbbc6b3181568d87a376ae2e814464c230

COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
COPY renderer/ ${LAMBDA_TASK_ROOT}/renderer/
COPY services/ ${LAMBDA_TASK_ROOT}/services/

RUN pip install --no-cache-dir "${LAMBDA_TASK_ROOT}[services,transcribe]"

# Model weights are baked in at build time (network access here, none at
# Lambda cold start -- the VPC has no IGW/NAT route, so a runtime download
# would hang until timeout instead of failing fast;
# renderer/transcribe.py's local_files_only=True depends on this).
#
# `download_model(output_dir=...)`, not `WhisperModel(download_root=...)`:
# the latter lays weights out under a nested HF-hub cache path
# (models--Systran--faster-whisper-small/snapshots/<hash>/model.bin), which
# `WhisperModel('/opt/whisper-model', local_files_only=True)` can't load
# ("Unable to open file 'model.bin'"). download_model saves a flat
# directory that the runtime call actually reads.
RUN python -c "from faster_whisper.utils import download_model; \
    download_model('small', output_dir='/opt/whisper-model')"

# overridden per function by infra/envs/dev/lambda.tf's image_config.command,
# kept here so `docker run` without an override still does something sane
CMD ["services.analyze_transcribe.handler.handler"]
