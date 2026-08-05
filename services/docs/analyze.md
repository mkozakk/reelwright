# Analysis Branches (`analyze_loudness/`, `analyze_scenes/`, `analyze_transcribe/`)

Three structurally identical Lambdas, one per evidence type: download this source's extracted audio (or, for scenes, the raw video, since `scdet` needs real frames rather than the audio-only FLAC), run the matching `renderer/` analysis function, upload a small JSON artifact to `work/{job_id}/<category>/{src_id}.json`, and record its S3 key under `analysis_keys[category][src_id]` in DynamoDB.

## The three functions

- **`analyze_loudness/`** runs `renderer.loudness.run_loudness_analysis`, producing `{src_id, sample_interval_s, points: [{t, level_db}]}`.
- **`analyze_scenes/`** runs `renderer.scenes.run_scene_analysis` on the raw video, producing `{src_id, cuts: [{t, score}]}`.
- **`analyze_transcribe/`** runs `renderer.transcribe.run_transcription` plus `filter_segments`, producing `{src_id, segments: [...], words: [...]}`. Hallucinated segments are dropped **before** anything reaches S3, not filtered later by the planner.

## Conditional execution

`analyze_transcribe/` only runs when `subtitles_enabled`. `TranscribeChoice` in the state machine skips the whole branch otherwise, since transcript evidence is only needed to drive subtitle burn-in and phrase-level editorial evidence, both of which are pointless when subtitles are off.

## Related

- [[probe]]: extracts the audio/video these services read
- [[plan]]: consumes all three artifacts to build the LLM's evidence bundle
