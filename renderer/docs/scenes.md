# Scene Detection (`scenes.py`)

`run_scene_analysis` runs ffmpeg's `scdet` filter over a video file and turns its stderr output into scene-cut timestamps, the same stderr-scraping approach `loudness.py` uses since `scdet` also has no JSON output mode.

## Threshold

The filter runs at `scdet=threshold=0.05`, well below the filter's own default of 10. The default is tuned for typical broadcast footage and misses most cuts in low-motion phone footage, which is the primary source type this pipeline expects.

## Scraping stderr

Lines look like:

```
[scdet @ 0x...] lavfi.scd.score: 0.944, lavfi.scd.time: 0.0333333
```

`SCDET_LINE_RE` pulls the score and timestamp out of each `[scdet @` line into a `SceneCut`. The score is a confidence value the caller can use to rank cuts (the planning step's evidence builder does exactly this, keeping only the top-scoring cuts per source).

## Related

- [[probe]]: this module needs the raw video, not the audio-only FLAC `probe.py` extracts for other analyses
- [[loudness]]: the sibling analysis pass
