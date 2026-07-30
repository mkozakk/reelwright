# reelwright

AI-directed video montage service on AWS. Users upload footage; an LLM
produces an Edit Plan (JSON); a deterministic renderer compiles it into
ffmpeg operations. Portfolio project.

## Benchmarks

| Benchmark | Result |
|---|---|
| Whisper arm64 vs x86_64 (transcription wall-clock on sample audio) | TODO(benchmark): not yet run. `docker/transcribe.Dockerfile` ships `x86_64` as the working default (matches every other Lambda image in this repo today) until this is measured against real sample audio -- do not treat x86_64 as a locked architectural decision, and do not fill this row with invented numbers. |

## Manual post-deploy steps

- **X-Ray service map**: after a demo job runs end-to-end, open the X-Ray
  console, filter by the `job_id` annotation (`services/common/tracing.py`),
  and screenshot the service map -- this can't be produced from Terraform/CI,
  it needs a real trace to exist first.
- **SES sender verification**: `terraform apply` creates the
  `aws_ses_email_identity` for `var.ses_from_email` but AWS still emails that
  address a verification link -- click it before the `finish` Lambda's
  `ses:SendEmail` calls will succeed.
- **SES production access**: the account starts in the SES sandbox, which
  only sends to verified addresses. Once the sender is verified, file the
  AWS Support case to leave the sandbox -- safe to do now since every
  recipient is a Cognito account's own verified email (Stage E), never
  free-text user input.

