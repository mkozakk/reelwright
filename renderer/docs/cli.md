# The CLI (`cli.py`, `ffmpeg_run.py`)

`python -m renderer render <plan> <output> --source ID=PATH [--source ...] [--transcript ID=PATH] [--aspect ...] [--max-duration ...]` is the package's single subcommand and the fastest way to see a montage get built without deploying anything.

## What it does

It loads and validates the plan, with `--aspect`/`--max-duration` becoming the `prefs` override passed into `validate_plan`. If `subtitles.enabled`, it builds the `.ass` file next to the output (`output.with_suffix(".ass")`). It then compiles and runs ffmpeg. Validation errors and ffmpeg errors both print to stderr and return a non-zero exit code rather than raising past `main()`, so the CLI is scriptable.

This is the local, manual entrypoint for running the renderer against a plan and files on disk: demos, debugging, or a portfolio walkthrough. The deployed pipeline's `services/cut/` and `services/render/` don't shell out to it; they import `compile_plan`, `build_segment_plan`/`build_concat_plan`, and `run_ffmpeg` directly as a library, since they already have the plan and S3-downloaded files in hand and gain nothing from a subprocess round-trip.

## The ffmpeg subprocess wrapper (`ffmpeg_run.py`)

`ffmpeg_binary()`/`ffprobe_binary()` (the latter in `probe.py`) resolve the binary via `shutil.which` and raise immediately if it's not on `PATH`, with no silent fallback to a bundled or guessed path. `run_ffmpeg(args)` runs `[ffmpeg_binary(), *args]` via `subprocess.run`, capturing both streams, and raises `FfmpegError(returncode, stderr)` on nonzero exit. The stderr in that error is truncated to its last 4000 characters: long enough to contain the actual failure line, short enough not to flood logs.

## Related

- [[compile]]: produces the argv this module hands to ffmpeg
- [[edit-plan]]: validated here before anything is compiled
