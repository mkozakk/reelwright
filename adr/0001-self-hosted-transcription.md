# 0001: Self-hosted transcription instead of AWS Transcribe

## Status

Accepted.

## Context

The pipeline needs word-level timestamped transcripts for two things: subtitle burn-in and evidence for the planning LLM. AWS Transcribe is the obvious managed option: no infra to run, billed per minute of audio ($0.024/min at standard tier), integrates natively with the rest of the AWS pipeline.

Cost matters here more than it would on a normal product, because the whole point of this build is to stay near-free per job. At roughly 5 minutes of audio per job, Transcribe alone would run about $0.12/job, which was on track to be the single largest line item in the per-job cost breakdown.

The alternative is running `faster-whisper` (the `small` model, int8) ourselves, inside a Lambda container. That trades a fixed cost per job for a memory-sized Lambda invocation, and moves "does transcription work" from AWS's SLA onto our own container.

## Decision

Self-host transcription with faster-whisper `small` (int8) in a Lambda container, sized at 3008 MB. This is the primary and only transcription path; there is no Transcribe fallback.

3008 MB, not a rounder 4096 or the Lambda ceiling of 10240 MB, because this account's per-function `CreateFunction` limit is capped at 3008 MB - an account-level restriction, not a sizing choice. `small` int8 fits comfortably under that regardless.

## Consequences

- Transcription cost drops from ~$0.12/job to under $0.01/job, roughly a 24x reduction, and is the single biggest saving in the cost model.
- Audio never leaves our own compute, which is a better story for the privacy section than shipping user audio to a third-party transcription API.
- We own the model's accuracy and failure modes instead of inheriting AWS's. Whisper is known to hallucinate text over silence and music; the pipeline compensates by dropping low-confidence/high-no-speech-probability segments before they reach subtitles or planner evidence, a mitigation Transcribe's managed service wouldn't have required us to build.
- We own the container: pinned faster-whisper version, baked model weights, and cold-start/memory tuning are now our maintenance burden instead of AWS's.
- Architecture (arm64 vs x86_64) for the Whisper Lambda image was an open question rather than an assumed default, since CTranslate2 performance on NEON isn't guaranteed to match AVX2 and this is the one Lambda that dominates pipeline compute time. Measured below.

## Benchmarks

**arm64 vs x86_64** - `faster-whisper small` int8 on a 5-minute stereo sample, 3008 MB both ways:

| Benchmark | x86_64 | arm64 (Graviton2) |
|---|---|---|
| Wall-clock transcription time | 41.2s | 33.6s |
| Peak RSS | 2.1 GB | 1.9 GB |
| Cost per invocation (Lambda GB-seconds) | $0.0020 | $0.0013 |

arm64 is ~18% faster and ~35% cheaper per invocation once Graviton2's 20%-lower GB-second price compounds with the shorter runtime. `docker/transcribe.Dockerfile` still ships `x86_64` as the working default, matching every other Lambda image in this repo, but arm64 is the clear next move here specifically.

**Self-hosted vs AWS Transcribe** - same sample, x86_64 Whisper Lambda above vs. Transcribe's standard batch API (`StartTranscriptionJob` + poll for `COMPLETED`), same region:

| Benchmark | Self-hosted (this repo) | AWS Transcribe |
|---|---|---|
| Cost | $0.0020/job (compute only) | $0.12/job ($0.024/min) |
| Latency | 41.2s, synchronous in the pipeline | 68s (submission + queuing + polling) |
| Data locality | Never leaves the VPC (no NAT/IGW route) | Uploaded to AWS's managed service |

Raw compute cost alone is ~60x cheaper; the "~24x" figure above is the more conservative one, folding in request/S3 overhead this compute-only number doesn't. Either way it's the single biggest line item removed from the job cost, and the synchronous in-Lambda decode also wins on latency - Transcribe's per-job queuing overhead outweighs its managed-service convenience at this file size.
