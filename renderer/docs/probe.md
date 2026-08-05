# Probing & Upload Validation (`probe.py`)

`probe_file` wraps `ffprobe -show_streams -show_format` into a `ProbeResult` (kind, duration, dimensions, fps, codecs), and `validate_probe` is the boundary that decides whether an uploaded file is safe to feed into the rest of the pipeline.

## Upload limits (`validate_probe`)

| Check | Limit |
|---|---|
| File size | `MAX_FILE_BYTES` = 500 MB |
| Duration | `MAX_DURATION_SECONDS` = 300s |
| Pixel rate | `MAX_PIXEL_RATE` ≈ 4K30 (width × height × fps) |
| Video codec | `h264`, `hevc`, `vp9` |
| Audio codec | `aac`, `opus`, `mp3` |

A declared `kind` (video/audio) that doesn't match the probed kind is also rejected here, since the declaration comes from the client and is never trusted on its own.

## Two audio extractions

- `extract_audio_flac`: 16kHz mono FLAC, the analysis-ready format that Whisper, loudness, and scene analysis all read.
- `extract_audio_asset`: 48kHz stereo FLAC, used only for a user-uploaded audio source's own playback quality at Render time.

The two exist because analysis and playback have different quality needs, not because of a copy-paste: a mono 16kHz track is plenty for a loudness curve or a transcript, but not for the music track someone actually chose to include in their montage.

## Related

- [[loudness]] and [[scenes]] and [[transcribe]]: consume the FLAC/raw video this module extracts
- [[edit-plan]]: `validate_plan`'s source checks assume every source already passed through here
