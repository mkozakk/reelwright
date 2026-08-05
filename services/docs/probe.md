# Probe (`probe/`)

One Map iteration per source. `run_probe` downloads the raw upload, runs `renderer.probe.probe_file` plus `validate_probe` against it, and either rejects the upload or writes its real, probed metadata back onto the job.

## Never trust the presign

The declared `kind` and `size` a client sent at presign time are re-checked here against what ffprobe actually sees. On any validation error, the job is marked `FAILED` and `ProbeRejected` is raised, and this is **never retried into Fargate**: a rejected upload never reaches a paid compute step.

## On success

The probed `duration`, `width`, `height`, and `fps` are written back onto the source record (`dynamo.update_source`), and one of two audio extractions runs depending on kind:

- Video sources get a 16kHz mono FLAC (`extract_audio_flac`), and an `analysis_keys["audio"]` entry is recorded, since only video sources feed the Analyze branch, an audio-only asset has no editorial evidence value.
- Audio sources get a 48kHz stereo FLAC (`extract_audio_asset`) for their own playback quality at Render time, but no analysis entry.

## Related

- [[probe]] (renderer): the wrapped `probe_file`/`validate_probe` functions this service calls
- [[session-profile]]: runs once every source in the session has been through this Map
- [[analyze]]: the three branches that read this service's audio output
