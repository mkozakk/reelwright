# same pinned ffmpeg build as docker/lambda.Dockerfile -- reproducible
# renders need the same binary everywhere. Re-resolve base digest with:
#   skopeo inspect --format '{{.Digest}}' docker://docker.io/library/python:3.12-slim
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ARG FFMPEG_URL=https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
ARG FFMPEG_SHA256=abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl xz-utils ca-certificates \
    && curl -fsSL -o /tmp/ffmpeg.tar.xz "$FFMPEG_URL" \
    && echo "${FFMPEG_SHA256}  /tmp/ffmpeg.tar.xz" | sha256sum -c - \
    && mkdir /tmp/ffmpeg-extract \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg-extract --strip-components=1 \
    && cp /tmp/ffmpeg-extract/ffmpeg /tmp/ffmpeg-extract/ffprobe /usr/local/bin/ \
    && rm -rf /tmp/ffmpeg.tar.xz /tmp/ffmpeg-extract \
    && apt-get purge -y curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY renderer/ renderer/
COPY services/ services/
COPY assets/ assets/

RUN pip install --no-cache-dir ".[services]"

ENTRYPOINT ["python", "-m", "services.render"]
