# Loudness Analysis (`loudness.py`)

`run_loudness_analysis` runs ffmpeg's `ebur128` filter over an audio file and turns its verbose stderr output into a loudness curve, since the filter itself has no JSON output mode.

## Scraping stderr

ffmpeg logs a line per sample, like:

```
[Parsed_ebur128_0 @ 0x...] t: 0.0999375  TARGET:-23 LUFS    M:-120.7 S:-120.7  ...
```

`EBUR128_LINE_RE` pulls the timestamp (`t:`) and momentary loudness (`M:`) out of each matching line into a `LoudnessPoint`.

## Downsampling

`downsample_to_1hz` buckets raw samples into one point per second, averaging within each bucket. The planner needs an energy curve to reason about, not every individual ffmpeg sample, so this keeps the evidence bundle small without losing the shape of the curve.

## Related

- [[probe]]: extracts the audio file this module analyzes
- [[scenes]]: the sibling analysis pass, sharing the same stderr-scraping approach
