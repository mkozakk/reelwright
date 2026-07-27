# shared by probe/cut/finish -- same digest, different handler CMD per function
# base image pinned by digest, not the mutable "3.12" tag. Re-resolve with:
#   skopeo inspect --format '{{.Digest}}' docker://public.ecr.aws/lambda/python:3.12
FROM public.ecr.aws/lambda/python:3.12@sha256:0628ddc11919f85f4261517d356392dbbc6b3181568d87a376ae2e814464c230

# pinned by content hash, not just a version tag -- the upstream "release"
# URL is mutable, so this fails loudly instead of silently drifting.
# currently resolves to ffmpeg 7.0.2; bump both ARGs together to upgrade.
ARG FFMPEG_URL=https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
ARG FFMPEG_SHA256=abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67

RUN microdnf install -y tar xz \
    && curl -fsSL -o /tmp/ffmpeg.tar.xz "$FFMPEG_URL" \
    && echo "${FFMPEG_SHA256}  /tmp/ffmpeg.tar.xz" | sha256sum -c - \
    && mkdir /tmp/ffmpeg-extract \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg-extract --strip-components=1 \
    && cp /tmp/ffmpeg-extract/ffmpeg /tmp/ffmpeg-extract/ffprobe /usr/local/bin/ \
    && rm -rf /tmp/ffmpeg.tar.xz /tmp/ffmpeg-extract \
    && microdnf clean all

COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
COPY renderer/ ${LAMBDA_TASK_ROOT}/renderer/
COPY services/ ${LAMBDA_TASK_ROOT}/services/
COPY assets/ ${LAMBDA_TASK_ROOT}/assets/

RUN pip install --no-cache-dir "${LAMBDA_TASK_ROOT}[services]"

# overridden per function by infra/envs/dev/lambda.tf's image_config.command
CMD ["services.probe.handler.handler"]
