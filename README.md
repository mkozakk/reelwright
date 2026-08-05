# reelwright

reelwright is an AI-directed video editor. You upload raw footage and get back a cut, color-graded, subtitled montage. An LLM looks at the transcript, the loudness curve, and the scene changes, and decides what the edit should look like: which moments matter, where cuts land, what mood the color and music should carry.

The LLM never touches ffmpeg. It writes a JSON **Edit Plan**, the plan gets validated against a strict schema, and a deterministic renderer turns that plan into actual ffmpeg operations.

## What it does

A job starts with one or more video/audio files (up to 5, up to 5 minutes of media total) and an optional free-text brief, e.g. "open on the first scene from video 1, use the audio I uploaded as music, keep it gentle." Three analysis passes run in parallel on the source: self-hosted Whisper produces a word-timestamped transcript, ffmpeg's `ebur128` filter produces a loudness curve, and a scene-change detector marks hard cuts. Those signals, plus the user's brief, get bundled and sent to Amazon Nova Lite on Bedrock, forced into structured output via tool-use so the model can only ever produce a schema-shaped plan.

The plan itself is small: a list of clips with `start`/`end`/`reason`, a subtitle mode, a color preset, a music track and ducking setting, an output aspect and duration cap. Every field is enum-restricted or numerically clamped before it reaches ffmpeg.

The renderer itself is a plain Python package with no AWS dependencies in it (`renderer/`), and it runs standalone: `python -m renderer render plan.json output.mp4 --source src1=input.mp4`. It maps each plan field to one ffmpeg technique: cuts become accurate-seek re-encodes, transitions become `xfade`/`acrossfade`, subtitles are Whisper timestamps burned in through a generated `.ass` file, color grades are `.cube` LUTs plus clamped `eq` adjustments, music gets mixed in with `sidechaincompress` ducking under speech.

## Libraries

- [pydantic](https://github.com/pydantic/pydantic): the Edit Plan schema is a set of pydantic models. `tools/generate_schema.py` derives the JSON Schema handed to Bedrock's tool-use API from those models, and a test keeps the two in sync.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper): self-hosted transcription (the `small` model, int8) instead of a paid API. Runs in a Lambda container and produces the word-level timestamps both subtitles and the planner depend on.
- ffmpeg, called via subprocess (no Python binding): `ffprobe` for validation and metadata, `ebur128` for loudness, scene-change detection, and the render filtergraph (`xfade`, `zoompan`, `sidechaincompress`, `lut3d`, `subtitles=`).
- [boto3](https://github.com/boto/boto3): the AWS glue in `services/`, kept out of `renderer/`.
- [cryptography](https://github.com/pyca/cryptography): signs CloudFront playback/download URLs.
- [aws-xray-sdk](https://github.com/aws/aws-xray-sdk-python): tracing across the API, Step Functions, Lambdas, and the Fargate render task, correlated on job id.

## Architecture

```
        Frontend (S3 + CloudFront static site)
          │  POST /jobs → presigned upload URL(s)
          ▼
  API Gateway ── Lambda (job-api) ── DynamoDB (jobs table)
          │
   S3 raw-media bucket
          │  client calls POST /jobs/{id}/start once every file is uploaded
          ▼
   Lambda (trigger): verifies every source exists, conditional
   status flip UPLOADING → ANALYZING, StartExecution
          ▼
 ┌─────────────────── Step Functions: montage-pipeline ───────────────────┐
 │                                                                        │
 │ 1. Probe          ffprobe per source (Map): validate, duration,       │
 │                   resolution, pixel-rate cap, extract audio track      │
 │                                                                        │
 │ 2. Analyze        (parallel branch, per source)                        │
 │    ├─ Whisper     faster-whisper → word-level timestamps               │
 │    ├─ Loudness    ffmpeg ebur128 → energy curve                        │
 │    └─ Scenes      ffmpeg scene-change detector → timestamps            │
 │                                                                        │
 │ 3. Plan           merge per-source evidence → Bedrock (Nova Lite):     │
 │                   transcript + energy + scenes + user brief            │
 │                   → forced structured output via tool-use              │
 │                   → validate against the Edit Plan schema              │
 │                   → retry once with validation errors, else fall back  │
 │                                                                        │
 │ 4. Cut            batched re-encode per clip (always, never            │
 │                   stream-copy), normalized to a shared target profile  │
 │                                                                        │
 │ 5. Render         Fargate: concat with xfade, burn subtitles,          │
 │                   apply color grade, mix + duck music                  │
 │                   Spot, with retry → on-demand fallback                │
 │                                                                        │
 │ 6. Finish         verify output, thumbnail, update DynamoDB,           │
 │                   CloudFront signed URL                                │
 │                                                                        │
 │ Catch-all → mark job failed + DLQ (alarmed on queue depth)             │
 └────────────────────────────────────────────────────────────────────────┘
          │
   S3 output bucket → CloudFront (playback + download, signed URLs)
```


## Quick start

The renderer has no AWS dependency, so the fastest way to see a montage get built is entirely local:

```bash
git clone https://github.com/mkozakk/reelwright.git
cd reelwright
pip install -e ".[dev]"

python -m renderer render \
  assets/sample/plan_basic.json out.mp4 \
  --source src1=assets/sample/clip_a.mp4
```

That runs the same code path the Fargate render task runs in production, against a hand-written sample plan and a bundled clip. No AWS credentials, no deployed stack. `assets/sample/` also has a multi-clip plan with transitions (`plan_transitions.json`) and a fuller plan exercising subtitles, color, and music (`plan_full.json`).

### Deploying the AWS pipeline

Everything under `infra/envs/dev` is one Terraform environment. `terraform apply` provisions:

- **Storage**: the raw/work/output S3 buckets with their lifecycle rules, and the DynamoDB `jobs` table.
- **Pipeline**: the Step Functions state machine, the EventBridge rule that can trigger it, and the DynamoDB semaphore item that caps concurrent renders.
- **Compute**: every Lambda function (job API, trigger, probe, the three analyzers, plan, cut, finish), their container images pushed to ECR, and the ECS/Fargate task definition the render step runs.
- **API & auth**: API Gateway in front of the job API, and the Cognito user pool used for sign-in.
- **Frontend**: the S3 bucket and CloudFront distribution serving the static site, with signed URLs for playback and download.
- **Observability & cost control**: a CloudWatch dashboard and pipeline-failure alarm, an AWS Budget alarm, and the IAM role GitHub Actions assumes over OIDC for CI/CD.

```bash
cd infra/envs/dev
terraform init
terraform apply
```

It's a single `dev` environment on purpose. This is a portfolio build meant to be brought up for a demo and torn down (`terraform destroy`) between them, not run as a persistent service.

## Repository layout

```
reelwright/
├── renderer/          # AWS-free Python package, the heart. Edit Plan schema/models,
│                       # plan → ffmpeg filtergraph compiler, ASS subtitle generator,
│                       # LUTs/subtitle-styles/music manifest, runnable standalone
├── services/           # thin Lambda/Fargate shells around renderer/, all the AWS glue
│   ├── job_api/         # create job, presign uploads, get status
│   ├── trigger/         # verifies sources, flips job status, starts the state machine
│   ├── probe/            # ffprobe + audio extract
│   ├── analyze_loudness/ # ebur128 → energy curve
│   ├── analyze_scenes/   # scene-change detection
│   ├── analyze_transcribe/ # faster-whisper
│   ├── session_profile/  # picks the shared output profile for multi-source jobs
│   ├── plan/              # Bedrock call, schema validation, no-LLM fallback
│   ├── cut/               # segment extraction, always re-encoded
│   ├── render/             # Fargate entrypoint: pulls from S3, runs renderer/, reports status
│   ├── finish/             # ffprobe-verify output, thumbnail, CloudFront signed URL
│   ├── semaphore/          # DynamoDB-backed concurrency cap on the render step
│   └── analytics_sink/     # job lifecycle events → S3 (raw JSON) → Athena (OpenX SerDe, no Firehose)
├── infra/               # Terraform: modules + a single `dev` environment
├── adr/                  # short ADRs for the close-call decisions, inlined below
├── docker/               # Dockerfiles: shared Lambda image, Fargate render-task, Whisper image
├── frontend/             # vanilla JS static site: Cognito sign-in, upload, status polling, plan editor
├── assets/
│   ├── music/             # bundled royalty-free tracks + licenses.txt
│   └── sample/             # sample clips, sample plans, sample transcript for local runs
├── scripts/              # smoke/load test against real AWS, plan-fallback diagnosis, image/zip build
├── tools/                # generators for committed artifacts (schema.json, LUTs), build
│                          # tooling, not a component; both are regenerated and diffed in CI
├── tests/                # unit + snapshot (no ffmpeg) and e2e (`media` marker, runs in CI
│                          # against real ffmpeg)
└── .github/workflows/    # lint+test, container image builds, terraform plan/apply
```

Each of `renderer/`, `services/`, and `infra/` carries its own README and a
`docs/` tree documenting every feature:

- [renderer/README.md](renderer/README.md) - the AWS-free package: Edit Plan schema/validation, ffmpeg compilation, subtitles, presets, and analysis modules
- [services/README.md](services/README.md) - every Lambda/Fargate entrypoint, the Step Functions pipeline flow, and the shared `common/` library
- [infra/README.md](infra/README.md) - the Terraform stack: networking, storage, compute, orchestration, API/auth/delivery, IAM, and deploy flow

## Tech stack

| Component | Technology |
|-----------|-----------|
| Renderer / services | Python 3.12, pydantic, ffmpeg |
| Transcription | faster-whisper (self-hosted, in-container) |
| Planning model | Amazon Bedrock, Nova Lite, forced JSON via tool-use |
| Infra | Terraform, AWS (Step Functions, Lambda, Fargate, S3, DynamoDB, EventBridge, API Gateway, Cognito, CloudFront) |
| Frontend | static site (vanilla JS), S3 + CloudFront |
| CI/CD | GitHub Actions, OIDC federation into AWS (no long-lived keys) |

## Tests

Tests are placed in `tests/`, named for what they exercise (`test_renderer_*`, `test_services_*`). Made with plain pytest, fixtures in `conftest.py`, no custom test framework on top.

Most tests touch neither ffmpeg nor real AWS. The `renderer/` tests call `compile_plan()` directly and check the ffmpeg command it builds rather than actually running it, so a broken `xfade` or a dropped flag fails without needing a real video file. The `services/` tests mock AWS with [moto](https://github.com/getmoto/moto) instead: `conftest.py`'s `aws_stack` fixture spins up a real in-memory DynamoDB table and the three S3 buckets, so handler logic gets exercised against real DynamoDB/S3 semantics without ever touching an actual account. Just `pip install -e ".[dev,services]"` and run:

```bash
pytest -m "not media and not whisper"
```

Tests marked `@pytest.mark.media` take the same checks one step further by actually invoking ffmpeg against the sample clips in `assets/sample/` - this is the tier that catches a LUT that doesn't parse or an `xfade` ffmpeg itself rejects. They need ffmpeg/ffprobe on `PATH`, nothing else beyond the `dev,services` extras above:

```bash
pytest -m "media and not whisper"
```

`ci.yml` runs both of those on every push, so this is what "the test suite" means in CI - no manual step involved.

Tests marked `@pytest.mark.whisper` round-trip a real faster-whisper model and are excluded from CI entirely, since they need the baked model weights CI doesn't have. Run them manually once you have a model directory:

```bash
pip install -e ".[dev,services,transcribe]"
WHISPER_MODEL_DIR=/opt/whisper-model pytest -m whisper
```

Everything else that touches transcription stubs `run_transcription` instead of needing a real model.

`test_pipeline_end_to_end.py` is the odd one out - it runs the whole Probe → Analyze → Plan → Cut → Render → Finish pipeline in-process, once against the deterministic fallback (Bedrock stubbed as unreachable) and once against a multi-source job with a canned LLM response, checking that evidence from every source reaches the planner and that a user-uploaded music track actually gets picked up.

A few tests are deliberately adversarial rather than happy-path: an audio file declared as video at presign, a plan pointing at a source that doesn't exist, a declared file size nowhere near the real one. They exist because everything upstream of the Edit Plan schema is treated as untrusted, and they're what keeps that boundary honest as the code around it changes.

## CI/CD

Two GitHub Actions workflows, both authenticating to AWS over OIDC federation into a scoped IAM role — no long-lived AWS keys in repo secrets.

**`ci.yml`** runs on every push to `main` and every pull request: installs ffmpeg, runs the unit/snapshot suite (`not media and not whisper`), then the e2e suite against real ffmpeg (`media and not whisper`), and checks `terraform fmt -check -recursive` across `infra/`.

**`deploy.yml`** runs on push to `main` and on manual `workflow_dispatch`, as three jobs:

1. **`terraform-plan`** - assumes the plan-only OIDC role, runs `terraform plan` against `infra/envs/dev`.
2. **`build-and-push-images`** - builds and pushes three container images to ECR (`lambda`, `render-task`, `analyze-transcribe`), each tagged `latest` and by commit SHA.
3. **`apply`** - gated behind the `dev` GitHub Environment, which requires a manual reviewer approval before the job runs. Only this job can assume the write-capable deploy role, since its OIDC trust policy is scoped to `environment:dev`. It runs `terraform apply`, then syncs `frontend/` to the frontend S3 bucket and invalidates the CloudFront distribution - that step has to run last because `frontend/config.js` is a Terraform-generated file that doesn't exist until the apply produces it.

`terraform-plan` and the image build stay ungated so a broken plan or a broken image build surfaces on every push to `main`. Only the state-changing `apply` step needs a human to click approve.

## Architecture decisions

A handful of choices in here were genuine close calls - two or more real options, a cost or risk that made the choice non-obvious, and a decision that would be expensive to reverse later. Full records (context, decision, consequences) live in [`adr/`](adr/); the decisions themselves are summarized here.

**[0001](adr/0001-self-hosted-transcription.md) - Self-hosted transcription instead of AWS Transcribe.** Transcribe would run ~$0.12/job at $0.024/min; self-hosting `faster-whisper` (`small`, int8) in a Lambda container drops that under $0.01/job and keeps audio in-account, at the cost of owning Whisper's failure modes and the container ourselves. Benchmarks (arm64 vs x86_64, and vs. Transcribe directly) are in the ADR.

**[0002](adr/0002-single-planning-model.md) - One planning model, no quality/Pro toggle.** A spike measured Nova Pro against the actual validator instead of assuming "bigger is better": a 60% fallback rate vs. 0% for Lite, at ~20x the cost per plan. Ships Lite only; a future tier means a different model family, benchmarked the same way.

**[0003](adr/0003-cut-always-reencodes.md) - Cut always re-encodes, never stream-copies.** Stream-copy is faster but only cuts on keyframes; re-encoding lands cuts exactly where the plan says and doubles as a security boundary, since the Fargate renderer downstream then never touches raw, attacker-controlled upload bytes.

**[0004](adr/0004-shared-lambda-container-image.md) - One shared Lambda container image, not one per function.** `probe`, `cut`, `finish`, and `plan` all need ffmpeg or `renderer/`'s dependencies, so they share one image (same digest, different handler `CMD`) instead of near-identical per-function images multiplying CI/ECR overhead. Trade-off: a dependency change in any one handler redeploys all four.

**[0005](adr/0005-s3-standard-storage-only.md) - S3 Standard storage everywhere, no Glacier/IA tiering.** IA and Glacier both bill a minimum storage duration (30+/90+ days) that every bucket here expires well inside (48h/7d/30d), so tiering would cost more than Standard, not less.

## Cost model

Built to be near-free to idle and cheap per job. Per job, roughly 5 minutes of 1080p media:

| Component | Cost |
|---|---|
| Whisper transcription (self-hosted, Lambda container) | <$0.01 |
| Bedrock planning (Nova Lite, tool-use) | <$0.01 |
| Guardrails screening | <$0.01 |
| Analysis + cut Lambdas | ~$0.02 |
| Fargate Spot render | ~$0.01-0.02 |
| Step Functions, S3, CloudFront | ~$0.01 |
| **Total** | **≈ $0.05-0.09** |

## The Edit Plan

This is `assets/sample/plan_basic.json`, the simplest valid plan the schema accepts:

```json
{
  "version": "1",
  "summary": "Basic single clip smoke test",
  "clips": [
    {"source": "src1", "start": 1.0, "end": 5.0, "reason": "smoke test clip"}
  ],
  "subtitles": {"enabled": false},
  "color": {"preset": "none"},
  "audio": {"music_track": null, "duck_under_speech": false},
  "output": {"aspect": "16:9", "resolution": "720p", "max_duration": 10}
}
```

## Effects the planner can direct

Twelve capabilities, deliberately capped. Adding a thirteenth costs a schema field, renderer code, tests, and prompt guidance, so the list stays short and variety comes from presets (a new LUT, subtitle style, or music track) instead of new code paths.

| Effect | How the LLM invokes it | What ffmpeg does |
|---|---|---|
| Clip selection & trimming | `clips[].start/end/reason` | accurate-seek re-encode per segment |
| Transitions | `clips[].transition_out = {type, duration}` | `xfade` / `acrossfade` |
| Subtitles | `subtitles = {enabled, style, mode}` | Whisper timestamps → `.ass` → `subtitles=` burn-in (word-highlight uses ASS karaoke tags) |
| Color grading | `color = {preset, adjust}` | `.cube` LUT + clamped `eq` |
| Music + ducking | `audio = {music_track, music_gain_db, duck_under_speech}` | `amix` + `sidechaincompress` keyed on speech |
| Playback speed | `clips[].speed` (0.5-2.0) | `setpts` + `atempo` |
| Aspect / reframe | `output = {aspect, resolution}` | `crop`/`scale`/`pad` (16:9, 9:16, 1:1) |

What each one actually lets the model choose:

- **Clip selection & trimming.** Every clip is a `{source, start, end, reason}` window into one of the job's uploaded sources, cut with an accurate-seek re-encode (never stream-copy) so it lands exactly on the timestamps the plan gives, not the nearest keyframe. A plan holds up to 30 clips, each at least 0.5s long; `reason` is mandatory on every one and the frontend shows it next to the clip so a user can see why the model chose that moment.
- **Transitions.** `clips[].transition_out = {type, duration}`. `type` is `cut` (the default, no transition), `crossfade` (`xfade`), or `fade_to_black` (`acrossfade`). `duration` is capped at half the shorter of the two adjacent clips, so a transition can never consume a whole clip.
- **Subtitles.** `subtitles = {enabled, style, mode}`. `style` is `bold-bottom` or `lower-third`; `mode` is `phrase` (one caption line at a time) or `word_highlight` (karaoke-style, one word highlighted per beat via ASS tags). Source text is Whisper's word-level timestamps, burned in through a generated `.ass` file - never the model's own wording.
- **Color grading.** `color = {preset, adjust}`. `preset` is `none`, `cinematic`, `vivid`, or `bw`, each a bundled `.cube` LUT. `adjust` layers clamped `eq` tweaks on top of the preset: contrast 0.9-1.2, saturation 0.8-1.4, brightness -0.1-0.1 - enough range to have an effect, not enough to blow out a shot.
- **Music + ducking.** `audio = {music_track, music_gain_db, duck_under_speech}`. `music_track` is an id resolved against the bundled manifest, or a job-scoped `user:<src_id>` for a track the user uploaded themselves; never a path. Gain is clamped to -20…-8 dB, and ducking runs `sidechaincompress` keyed on the speech track so music drops under dialogue automatically rather than the model having to time it.
- **Playback speed.** `clips[].speed`, clamped 0.5x-2.0x, applied per clip via `setpts` (video) and `atempo` (audio) so pitch stays natural at both ends of the range.
- **Aspect / reframe.** `output = {aspect, resolution}`. `aspect` is `16:9`, `9:16`, or `1:1` via `crop`/`scale`/`pad`; `resolution` is `1080p` or `720p`. Unlike every other field, `aspect` and `max_duration` are always overwritten from the user's own job preferences after the model responds - never left as the model's call.

A second tier (beat-synced cuts, punch-in/dynamic zoom, speed ramps, freeze-frame callouts, transition SFX) is designed but not built yet. Each is scoped the same way: one plan field, one fixed ffmpeg technique, so the validation boundary doesn't loosen as the catalog grows.

