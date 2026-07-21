FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY renderer/ renderer/
COPY assets/ assets/

RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "renderer"]
