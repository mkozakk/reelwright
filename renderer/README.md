# Renderer (`renderer/`)

The renderer is the deterministic heart of the pipeline: it turns a validated Edit Plan into a real ffmpeg command and, given the same plan and the same source files, always produces the same output. It is a plain Python package with no AWS dependency at all, no `boto3` import anywhere in it, so it runs standalone on any machine with `ffmpeg`/`ffprobe` on `PATH`. `services/` wraps it with the AWS glue (S3 fetch, DynamoDB status, Bedrock calls); this package never needs to know AWS exists.

## Directory Structure

```text
renderer/
├── docs/                    # Full documentation of all renderer features
├── edit_plan/
│   ├── models.py            # pydantic schema for the Edit Plan
│   └── validate.py          # clamping + structural/source validation
├── compile.py                # Edit Plan -> ffmpeg filter_complex command
├── subtitles.py               # word-timestamped transcript -> .ass file
├── presets/
│   ├── color.py               # color preset name -> .cube LUT path
│   ├── music.py                # music track id -> bundled asset path
│   ├── music_manifest.json      # id -> {file, mood}
│   └── luts/                    # bundled .cube files (generated, committed)
├── probe.py                   # ffprobe wrapper + upload validation
├── loudness.py                 # ebur128 wrapper -> loudness curve
├── scenes.py                    # scdet wrapper -> scene-cut timestamps
├── transcribe.py                 # faster-whisper wrapper + hallucination filter
├── segments.py                    # per-clip / concat plan rewriting for Cut -> Render
├── thumbnail.py                    # single-frame thumbnail extraction
├── ffmpeg_run.py                    # subprocess wrapper shared by every ffmpeg caller
└── cli.py, __main__.py               # `python -m renderer render ...` entrypoint
```

## Key Responsibilities

- **Edit Plan schema and validation**: a pydantic model of the only contract the LLM can speak through, plus a validator that clamps numeric drift and rejects anything structurally unsafe before it reaches ffmpeg.
- **Compilation**: a pure function from a validated plan to one ffmpeg `filter_complex` command, covering cuts, transitions, color, subtitles, and music in a fixed five-stage pipeline.
- **Subtitles**: converts Whisper's word timestamps into a karaoke- or phrase-mode `.ass` file, sanitizing text so transcript content can never inject ASS override tags.
- **Analysis**: three thin ffmpeg-stderr scrapers (loudness, scene cuts) plus a faster-whisper wrapper, all feeding the evidence the LLM planner reasons over.
- **Presets**: name-to-asset resolution for color LUTs and music tracks, deliberately failing loud on a missing asset instead of silently no-opping.
- **Segment rewriting**: the glue that lets Cut re-encode one clip at a time while Render still calls the exact same `compile_plan` on the reassembled whole.

## Features & Internal Documentation

* **[The Edit Plan](docs/edit-plan.md)** - the pydantic schema, and the three-pass validator (preference overwrite, clamping, structural + source checks) (`edit_plan/`).
* **[Compiling to ffmpeg](docs/compile.md)** - the five-stage `compile_plan` pipeline: clips, junctions, color, subtitles, music, and the label allocator (`compile.py`).
* **[Subtitles](docs/subtitles.md)** - word timestamps to `.ass`: line breaking, phrase vs. karaoke mode, and text sanitization (`subtitles.py`).
* **[Presets](docs/presets.md)** - color LUT and music track resolution, and why both raise on a miss instead of falling back (`presets/`).
* **[Probing & Upload Validation](docs/probe.md)** - the ffprobe wrapper, upload limits, and the two audio extractions (`probe.py`).
* **[Loudness Analysis](docs/loudness.md)** - the `ebur128` wrapper and the 1 Hz downsampling (`loudness.py`).
* **[Scene Detection](docs/scenes.md)** - the `scdet` wrapper and its tuned threshold (`scenes.py`).
* **[Transcription](docs/transcribe.md)** - the faster-whisper wrapper and the hallucination filter (`transcribe.py`).
* **[Segment Rewriting](docs/segments.md)** - how a plan is split for Cut and reassembled for Render, plus the thumbnail helper (`segments.py`, `thumbnail.py`).
* **[The CLI](docs/cli.md)** - `python -m renderer render`, and the shared ffmpeg subprocess wrapper (`cli.py`, `ffmpeg_run.py`).

## Why AWS-free

Keeping `renderer/` free of `boto3` is a boundary, not a style preference: it means the whole edit-to-video pipeline can be exercised locally, in a unit test, or in a portfolio demo, without deploying anything or touching a real AWS account. `services/cut/` and `services/render/` both import this package as a library (`compile_plan`, `build_segment_plan`/`build_concat_plan`, `run_ffmpeg`) rather than shelling out to its CLI, since they already have the plan and S3-downloaded files in hand by the time they call it.

## Quick start

```bash
pip install -e ".[dev]"
python -m renderer render \
  assets/sample/plan_basic.json out.mp4 \
  --source src1=assets/sample/clip_a.mp4
```

## Tech stack

| Concern | Technology |
|---|---|
| Schema & validation | pydantic |
| Video/audio processing | ffmpeg, ffprobe (subprocess, no Python binding) |
| Transcription | faster-whisper (`small`, int8) |
| CLI | argparse |

## Related Components

- **[Services](../services/README.md)**: the thin Lambda/Fargate shells that call this package with real files pulled from S3 and write results back to DynamoDB.
- **[Infra](../infra/README.md)**: provisions the Lambda/Fargate compute this package actually runs on, and the buckets its inputs and outputs pass through.
