# Transcription (`transcribe.py`)

`run_transcription` loads faster-whisper's `small` model in `int8` and returns per-segment transcription results with word-level timestamps, the raw material both subtitles and the planner's phrase-level evidence are built from.

## Loading the model

The model loads with `local_files_only=True`, which is load-bearing rather than incidental: without it, a missing or corrupt baked model would silently attempt a network call that the no-egress VPC blocks, hanging the Lambda until timeout instead of failing fast with a clear error.

## Filtering hallucinations

`filter_segments` drops a segment only when **both** conditions hold: `no_speech_prob > 0.6` **and** `avg_logprob <= -1.0`. These are OpenAI's own published thresholds for Whisper. Either signal alone isn't enough, since a confidently-decoded segment over background music can legitimately have an elevated `no_speech_prob` without actually being a hallucination; requiring both catches the segments that are genuinely made up.

## Flattening for consumers

`words_from_segments` flattens the kept segments into a flat `{start, end, text}` list, the shape both `subtitles.py` and the planner's evidence builder expect, so neither has to know about segment-level structure.

## Related

- [[probe]]: extracts the 16kHz mono FLAC this module transcribes
- [[subtitles]]: consumes the word list this module produces
